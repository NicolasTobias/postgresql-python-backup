#!/usr/bin/env python3
import os
import sys
import subprocess
import shlex
import datetime
import tempfile
import pathlib
import boto3
import tarfile

from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

# ------------ Config por entorno ------------
PGHOST = os.getenv("PGHOST", "postgres")
PGPORT = os.getenv("PGPORT", "5432")
PGUSER = os.getenv("PGUSER", "postgres")
PGPASSWORD = os.getenv("PGPASSWORD", "")
PGSSLMODE = os.getenv("PGSSLMODE", "prefer")  # prefer|require|disable

# S3 / Scaleway
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "fr-par")
S3_ENDPOINT = os.getenv("S3_ENDPOINT")  # ej: https://s3.fr-par.scw.cloud
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "pg-backups")
S3_SSE = os.getenv("S3_SSE", "AES256")

# Retención (opcional): borra objetos con más de N días
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "0"))  # 0 = no borrar
INSTANCE_NAME = os.getenv("INSTANCE_NAME", f"{PGHOST}-{PGPORT}")
PG_DUMP_JOBS = int(os.getenv("PG_DUMP_JOBS", "4"))

# Rutas a binarios
PSQL = os.getenv("PSQL_BIN", "psql")
PG_DUMP = os.getenv("PG_DUMP_BIN", "pg_dump")
PG_DUMPALL = os.getenv("PG_DUMPALL_BIN", "pg_dumpall")

# --------------------------------------------
os.environ["PGPASSWORD"] = PGPASSWORD  # exporta password a libpq


def run(cmd: str) -> subprocess.CompletedProcess:
    print(f"[cmd] {cmd}", flush=True)
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=False)


def list_databases() -> list:
    q = r"""SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"""
    cmd = f'{PSQL} "host={PGHOST} port={PGPORT} user={PGUSER} dbname=postgres sslmode={PGSSLMODE}" -Aqt -c "{q}"'
    p = run(cmd)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        raise RuntimeError("No se pudieron listar las bases de datos")
    dbs = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    return dbs


from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

def s3_client():
    if not (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and S3_BUCKET and S3_ENDPOINT):
        raise RuntimeError("Faltan variables S3: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT")
    session = boto3.session.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION or None,
    )
    return session.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"}
        )
    )

def upload_file(s3, local_path: pathlib.Path, key: str):
    extra_args = {}
    # Solo añade SSE si está configurado explícitamente
    if S3_SSE and S3_SSE.upper() in ("AES256", "aws:kms"):
        extra_args["ServerSideEncryption"] = S3_SSE

    print(f"[upload] s3://{S3_BUCKET}/{key} <- {local_path}")
    try:
        s3.upload_file(str(local_path), S3_BUCKET, key, ExtraArgs=extra_args)
    except ClientError as e:
        # Mensaje más explícito
        raise RuntimeError(
            f"Failed to upload {local_path} to {S3_BUCKET}/{key}: {e.response.get('Error', {}).get('Code')} - {e.response.get('Error', {}).get('Message')}"
        ) from e
    except ParamValidationError as e:
        # Por si hay un valor SSE inválido
        raise RuntimeError(f"Param validation error during upload: {e}") from e


def delete_older_than(s3, prefix: str, days: int):
    if days <= 0:
        return
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    print(f"[retention] Borrando objetos en s3://{S3_BUCKET}/{prefix} más antiguos que {days} días (<= {cutoff.isoformat()}Z)")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=datetime.UTC) <= cutoff:
                print(f"[retention] delete {obj['Key']}")
                s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])


def dump_globals(tempdir: pathlib.Path) -> pathlib.Path:
    out = tempdir / "globals.sql"
    cmd = (
        f'{PG_DUMPALL} --globals-only '
        f'--host {PGHOST} --port {PGPORT} --username {PGUSER} --no-password '
        f'--quote-all-identifiers '
        f'-f {shlex.quote(str(out))}'
    )
    p = run(cmd)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        raise RuntimeError("Falló pg_dumpall --globals-only")
    return out


def dump_database(db: str, tempdir: pathlib.Path) -> pathlib.Path:
    out_dir = tempdir / f"{db}.dir"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = (
        f'{PG_DUMP} --format=directory --jobs={PG_DUMP_JOBS} '
        f'--host {PGHOST} --port {PGPORT} --username {PGUSER} --no-password '
        f'--blobs --quote-all-identifiers --verbose '
        f'--file {shlex.quote(str(out_dir))} {shlex.quote(db)}'
    )
    p = run(cmd)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        raise RuntimeError(f"Falló pg_dump de {db}")

    # Empaquetar a tar.gz para subir un solo objeto
    tar_path = tempdir / f"{db}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(out_dir, arcname=f"{db}")
    return tar_path

def main():
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    base_prefix = f"{S3_PREFIX}/{INSTANCE_NAME}/{ts}"
    s3 = s3_client()

    with tempfile.TemporaryDirectory() as tmp:
        tempdir = pathlib.Path(tmp)

        # 1) Globals (roles, tablespaces, etc.)
        globals_path = dump_globals(tempdir)
        upload_file(s3, globals_path, f"{base_prefix}/globals.sql")

        # 2) Todas las DBs
        dbs = list_databases()
        print(f"[info] Bases encontradas: {dbs}")
        for db in dbs:
            path = dump_database(db, tempdir)
            upload_file(s3, path, f"{base_prefix}/{db}.dump")

    # 3) Retención opcional
    try:
        delete_older_than(s3, prefix=f"{S3_PREFIX}/{INSTANCE_NAME}/", days=RETENTION_DAYS)
    except ClientError as e:
        print(f"[retention] Aviso: {e}", file=sys.stderr)

    print("[done] Backup completo sin bloquear escrituras (pg_dump usa snapshot MVCC).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[fatal] {e}", file=sys.stderr)
        sys.exit(1)


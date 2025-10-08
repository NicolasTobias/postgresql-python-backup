# 🐘 PostgreSQL Backup to S3 (Scaleway Compatible)

Este proyecto contiene un contenedor de **Python 3.12** que realiza copias de seguridad automáticas de un servidor **PostgreSQL 17.5** y las sube a un bucket **S3-compatible (Scaleway)**.  
Se ejecuta como **CronJob de Kubernetes** cada 24h, haciendo dumps de todas las bases en formato **directorio (`-Fd`)** con **paralelismo (`-j N`)**, y subiéndolos comprimidos (`.tar.gz`) a S3.

---

## 🚀 Características principales

- Backup de **todas las bases de datos** sin bloqueo (usa snapshots MVCC)
- Backup de **roles y objetos globales** (`pg_dumpall --globals-only`)
- Subida a **S3-compatible** (Scaleway, MinIO, AWS, etc.)
- Formato **directorio + tar.gz** con paralelismo configurable
- **Retención automática** por días (`RETENTION_DAYS`)
- Configuración mediante **ConfigMap** y **Secret**
- Soporte de **SSE (AES256 o KMS)**

---

## 📁 Estructura

```
.
├── backup.py          # Script principal
├── Dockerfile         # Imagen Python + cliente PostgreSQL
├── requirements.txt   # Dependencias mínimas (boto3)
├── cronjob.yaml       # CronJob de Kubernetes
├── pg-backup-config.yaml
├── pg-backup-secrets.yaml
└── README.md
```

---

## ⚙️ Variables de entorno principales

| Variable | Descripción |
|-----------|-------------|
| `PGHOST` | Host o servicio de PostgreSQL |
| `PGPORT` | Puerto del servidor |
| `PGUSER` | Usuario con permisos de lectura global (pg_read_all_data) |
| `PGPASSWORD` | Password del usuario |
| `PGSSLMODE` | prefer / require / disable |
| `AWS_ACCESS_KEY_ID` | Access key de Scaleway |
| `AWS_SECRET_ACCESS_KEY` | Secret key de Scaleway |
| `S3_ENDPOINT` | Endpoint S3 (ej. https://s3.fr-par.scw.cloud) |
| `S3_BUCKET` | Nombre del bucket |
| `S3_PREFIX` | Prefijo dentro del bucket |
| `S3_SSE` | "" / AES256 / aws:kms |
| `AWS_KMS_KEY_ID` | ID de la key KMS (opcional) |
| `RETENTION_DAYS` | Días para mantener backups (0 = no borrar) |
| `INSTANCE_NAME` | Nombre lógico de la instancia |
| `PG_DUMP_JOBS` | Hilos de paralelismo (sólo aplica a formato directorio) |

---

## 🧱 Construcción multi-arquitectura

### Build sólo para ARM64 (Mac M2)
```bash
docker buildx build --platform linux/arm64 -t ghcr.io/tu-org/pgbackup:arm64-latest .
```

### Build multi-arch (AMD64 + ARM64)
```bash
docker buildx build   --platform linux/amd64,linux/arm64   -t ghcr.io/tu-org/pgbackup:latest   --push .
```

### Build sólo para AMD64 (servidores)
```bash
docker buildx build   --platform linux/amd64   -t ghcr.io/tu-org/pgbackup:amd64-latest   --push .
```

---

## 🧾 Ejemplo de CronJob (namespace `databases`)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: pg-backup-daily
  namespace: databases
spec:
  schedule: "15 2 * * *"   # Todos los días 02:15 UTC
  successfulJobsHistoryLimit: 2
  failedJobsHistoryLimit: 3
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: Never
          imagePullSecrets:
            - name: ghcr-cred
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            seccompProfile:
              type: RuntimeDefault
          containers:
            - name: backup
              image: ghcr.io/tu-org/pgbackup:latest
              imagePullPolicy: IfNotPresent
              envFrom:
                - secretRef:
                    name: pg-backup-secrets
                - configMapRef:
                    name: pg-backup-config
              resources:
                requests:
                  cpu: "100m"
                  memory: "256Mi"
                limits:
                  cpu: "1"
                  memory: "1Gi"
              volumeMounts:
                - name: temp
                  mountPath: /tmp
          volumes:
            - name: temp
              emptyDir: {}
```

---

## 🔒 Ejemplo de Secret y ConfigMap

### Secret (`pg-backup-secrets.yaml`)
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: pg-backup-secrets
  namespace: databases
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "<ACCESS_KEY>"
  AWS_SECRET_ACCESS_KEY: "<SECRET_KEY>"
  PGHOST: "postgresql.databases.svc.cluster.local"
  PGPORT: "5432"
  PGUSER: "postgres"
  PGPASSWORD: "<PASSWORD>"
  PGSSLMODE: "prefer"
```

### ConfigMap (`pg-backup-config.yaml`)
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: pg-backup-config
  namespace: databases
data:
  S3_ENDPOINT: "https://s3.fr-par.scw.cloud"
  S3_BUCKET: "level5-database-backups"
  S3_PREFIX: "postgres/arenero"
  INSTANCE_NAME: "level5-k8s"
  PG_DUMP_JOBS: "4"
  RETENTION_DAYS: "14"
```

---

## 🧠 Restore rápido

1. Descarga el `.tar.gz` de la base que quieras restaurar.
2. Descomprime el contenido:
   ```bash
   tar -xzf alpha.tar.gz
   ```
3. Restaura con paralelismo:
   ```bash
   pg_restore -d alpha -j 4 alpha.dir
   ```
4. Aplica los roles y objetos globales:
   ```bash
   psql -f globals.sql postgres
   ```

---

## ✅ Dependencias mínimas

`requirements.txt`

```txt
boto3>=1.34.0
```

---

## 🧩 License

MIT © 2025 — Preparado para PostgreSQL 17.5 + Scaleway Object Storage

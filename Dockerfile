# Imagen slim + cliente PostgreSQL 17
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

# Instala utilidades y configura el repo oficial de PostgreSQL con signed-by
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg dirmngr \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
     | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
  && sh -c 'echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo ${VERSION_CODENAME})-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
  && apt-get update \
  && apt-get install -y --no-install-recommends postgresql-client-17 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backup.py .
RUN chmod +x /app/backup.py

# Seguridad básica
RUN useradd -u 10001 -m appuser
USER appuser

ENTRYPOINT ["python", "/app/backup.py"]


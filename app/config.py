"""Configuração via variáveis de ambiente (definidas no docker-compose.yml)."""

import os
from pathlib import Path

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
MAX_DOWNLOAD_MB = int(os.environ.get("MAX_DOWNLOAD_MB", "200"))
MAX_EXTRACT_MB = int(os.environ.get("MAX_EXTRACT_MB", "500"))
COMPILE_TIMEOUT_S = int(os.environ.get("COMPILE_TIMEOUT_S", "300"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "2"))
KEEP_WORK = os.environ.get("KEEP_WORK", "false").strip().lower() in ("1", "true", "yes")
JOBS_QUOTA_MB = int(os.environ.get("JOBS_QUOTA_MB", "1000"))  # cota total de jobs/ (0 = sem limite)
JOBS_MAX_AGE_H = float(os.environ.get("JOBS_MAX_AGE_H", "0"))  # expira jobs por idade (0 = nunca)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip() or None

_default_jobs = Path(__file__).resolve().parent.parent / "jobs"
JOBS_DIR = Path(os.environ.get("JOBS_DIR", str(_default_jobs)))

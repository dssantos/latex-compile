"""Gerenciador de jobs: pool de threads, persistência em meta.json, pipeline."""

import json
import logging
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import compiler, config, detect, extract, sources
from .models import Job, JobState

log = logging.getLogger(__name__)

ACTIVE_STATES = {JobState.queued, JobState.running}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


class JobBusyError(Exception):
    """Job em execução não pode ser removido."""


class JobManager:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=config.MAX_WORKERS, thread_name_prefix="compile")

    # ---------- persistência ----------

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _save(self, job: Job) -> None:
        d = self.job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "meta.json.tmp"
        tmp.write_text(job.model_dump_json(indent=2))
        tmp.replace(d / "meta.json")  # write atômico

    def reload(self) -> None:
        """Recarrega o histórico do disco no startup; jobs órfãos viram erro."""
        loaded = 0
        for meta in sorted(self.jobs_dir.glob("*/meta.json")):
            try:
                job = Job.model_validate_json(meta.read_text())
            except Exception:
                log.warning("meta.json inválido, ignorando: %s", meta)
                continue
            if job.state in ACTIVE_STATES:
                job.state = JobState.error
                job.error = "interrompido por reinício do servidor"
                job.finished_at = _now()
                if not config.KEEP_WORK:
                    shutil.rmtree(self.job_dir(job.id) / "work", ignore_errors=True)
                self._save(job)
            self._jobs[job.id] = job
            loaded += 1
        if loaded:
            log.info("histórico recarregado: %d jobs", loaded)
        self.enforce_limits()

    # ---------- criação / consulta ----------

    def new_job(
        self,
        *,
        source: str,
        original_filename: str | None = None,
        source_url: str | None = None,
        client_id: str | None = None,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            source=source,
            original_filename=original_filename,
            source_url=source_url,
            client_id=client_id,
            created_at=_now(),
        )
        with self._lock:
            self._jobs[job.id] = job
        self.job_dir(job.id).mkdir(parents=True, exist_ok=True)
        self._save(job)
        return job

    def enqueue(self, job_id: str, main_tex_override: str | None) -> None:
        self._pool.submit(self._run_job, job_id, main_tex_override)

    def discard(self, job_id: str) -> None:
        """Remove job criado mas ainda não enfileirado (falha de upload)."""
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def delete(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.state in ACTIVE_STATES:
            raise JobBusyError(job_id)
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def log_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "build.log"

    def read_log(self, job_id: str) -> str | None:
        p = self.log_path(job_id)
        if not p.is_file():
            return None
        return p.read_text(errors="replace")

    def read_log_tail(self, job_id: str, lines: int = 200) -> str | None:
        text = self.read_log(job_id)
        if text is None:
            return None
        return "\n".join(text.splitlines()[-lines:])

    def pdf_path(self, job_id: str) -> Path | None:
        p = self.job_dir(job_id) / "output.pdf"
        return p if p.is_file() else None

    def total_size(self) -> int:
        return _dir_size(self.jobs_dir)

    # ---------- retenção (cota de espaço e idade máxima) ----------

    def enforce_limits(self) -> None:
        """Apaga jobs antigos por idade e, se acima da cota, os mais antigos primeiro.

        Jobs em fila ou em execução nunca são afetados. Chamado no startup e
        após a conclusão de cada job.
        """
        with self._lock:
            candidates = list(self._jobs.values())

        # 1. expiração por idade
        if config.JOBS_MAX_AGE_H > 0:
            cutoff = datetime.now(timezone.utc).timestamp() - config.JOBS_MAX_AGE_H * 3600
            for job in candidates:
                if job.state in ACTIVE_STATES:
                    continue
                ref = (job.finished_at or job.created_at)
                try:
                    ts = datetime.fromisoformat(ref).timestamp()
                except (TypeError, ValueError):
                    continue
                if ts < cutoff:
                    log.info("job %s expirado (idade máxima %.1fh)", job.id, config.JOBS_MAX_AGE_H)
                    self._evict(job.id)

        # 2. cota de espaço — remove os concluídos mais antigos até caber
        if config.JOBS_QUOTA_MB <= 0:
            return
        quota = config.JOBS_QUOTA_MB * 1024 * 1024
        remaining = list(self._jobs.values())
        sizes = {j.id: _dir_size(self.job_dir(j.id)) for j in remaining}
        total = sum(sizes.values())
        if total <= quota:
            return
        finished = sorted(
            (j for j in remaining if j.state not in ACTIVE_STATES),
            key=lambda j: j.finished_at or j.created_at,
        )
        for job in finished:
            if total <= quota:
                break
            log.info("cota de %d MB excedida: removendo job %s", config.JOBS_QUOTA_MB, job.id)
            self._evict(job.id)
            total -= sizes.get(job.id, 0)

    def _evict(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    # ---------- pipeline ----------

    def _finish(self, job: Job, state: JobState, error: str | None = None) -> None:
        job.state = state
        job.error = error
        job.finished_at = _now()
        if job.started_at:
            started = datetime.fromisoformat(job.started_at)
            job.duration_s = round(
                (datetime.now(timezone.utc) - started).total_seconds(), 1
            )
        if not config.KEEP_WORK:
            # em qualquer estado final o diretório de build não é mais necessário
            shutil.rmtree(self.job_dir(job.id) / "work", ignore_errors=True)
        self._save(job)

    def _run_job(self, job_id: str, main_tex_override: str | None) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        d = self.job_dir(job_id)
        work = d / "work"
        try:
            job.state = JobState.running
            job.started_at = _now()
            self._save(job)

            # 1. obter o zip
            zip_path = d / "source.zip"
            if job.source == "url":
                try:
                    sources.download(job.source_url or "", zip_path)
                except sources.DownloadError as exc:
                    self._finish(job, JobState.error, f"download falhou: {exc}")
                    return

            # 2. validar e extrair
            if not zipfile.is_zipfile(zip_path):
                self._finish(job, JobState.error, "o arquivo não é um zip válido")
                return
            try:
                extract.safe_extract(zip_path, work)
            except extract.ZipError as exc:
                self._finish(job, JobState.error, f"falha ao extrair zip: {exc}")
                return

            # 3. detectar o main .tex
            main_tex, candidates = detect.find_main_tex(work, main_tex_override)
            job.main_tex_candidates = candidates
            if not main_tex:
                if main_tex_override:
                    msg = (
                        f"arquivo principal '{main_tex_override}' não encontrado no projeto"
                        + (f"; candidatos: {', '.join(candidates)}" if candidates else "")
                    )
                else:
                    msg = "nenhum .tex com \\documentclass encontrado no projeto"
                self._finish(job, JobState.error, msg)
                return
            job.main_tex = main_tex
            self._save(job)

            # 4. compilar
            result = compiler.run_latexmk(work, main_tex, self.log_path(job_id))
            job.returncode = result.returncode
            if result.timed_out:
                job.timeout = True
                self._finish(job, JobState.timeout, result.error)
                return
            if result.returncode != 0 or result.pdf_path is None:
                self._finish(job, JobState.error, f"compilação falhou: {result.error}")
                return

            # 5. publicar o PDF
            output = d / "output.pdf"
            shutil.copyfile(result.pdf_path, output)
            job.pdf_size = output.stat().st_size
            self._finish(job, JobState.success)
        except Exception as exc:  # rede de segurança: nenhum job pode morrer em silêncio
            log.exception("erro inesperado no job %s", job_id)
            self._finish(job, JobState.error, f"erro interno: {exc}")
        finally:
            self.enforce_limits()


manager = JobManager(config.JOBS_DIR)

"""Endpoints REST do serviço."""

import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response

from . import extract
from . import config
from .config import MAX_UPLOAD_MB
from .jobs import JobBusyError, manager
from .models import Job, UrlJobRequest

router = APIRouter(prefix="/api")

MB = 1024 * 1024


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/storage")
def storage() -> dict:
    return {
        "used_bytes": manager.total_size(),
        "quota_bytes": config.JOBS_QUOTA_MB * 1024 * 1024,
        "max_age_h": config.JOBS_MAX_AGE_H,
    }


@router.post("/jobs/upload", status_code=202)
async def upload_job(
    request: Request,
    file: UploadFile = File(...),
    main_tex: str = Form(""),
    client_id: str = Form(""),
) -> Job:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_MB * MB:
        raise HTTPException(413, f"upload excede o limite de {MAX_UPLOAD_MB} MB")
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "envie um arquivo .zip")

    job = manager.new_job(
        source="upload",
        original_filename=file.filename,
        client_id=client_id.strip()[:64] or None,
    )
    dest = manager.job_dir(job.id) / "source.zip"
    size = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(MB):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * MB:
                    raise HTTPException(413, f"upload excede o limite de {MAX_UPLOAD_MB} MB")
                out.write(chunk)
    except HTTPException:
        manager.discard(job.id)
        raise
    except OSError as exc:
        manager.discard(job.id)
        raise HTTPException(500, f"falha ao gravar upload: {exc}") from exc

    try:
        names = extract.list_names(dest)
    except zipfile.BadZipFile:
        manager.discard(job.id)
        raise HTTPException(400, "o arquivo não é um zip válido") from None
    if not any(n.lower().endswith(".tex") for n in names):
        manager.discard(job.id)
        raise HTTPException(400, "o zip não contém nenhum arquivo .tex")

    manager.enqueue(job.id, main_tex.strip() or None)
    return job


@router.post("/jobs/url", status_code=202)
async def create_url_job(body: UrlJobRequest) -> Job:
    from urllib.parse import urlparse

    parsed = urlparse(body.url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "informe uma URL http(s) válida")

    job = manager.new_job(
        source="url",
        source_url=body.url.strip(),
        client_id=(body.client_id or "").strip()[:64] or None,
    )
    manager.enqueue(job.id, (body.main_tex or "").strip() or None)
    return job


@router.get("/jobs")
def list_jobs(client_id: str | None = None) -> list[Job]:
    jobs = manager.list()
    if client_id:
        # lista apenas os jobs criados por este cliente (navegador)
        jobs = [j for j in jobs if j.client_id == client_id]
    return jobs


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, tail: int = 200) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "job não encontrado")
    data = job.model_dump()
    data["log_tail"] = manager.read_log_tail(job_id, lines=max(1, min(tail, 2000)))
    return data


@router.get("/jobs/{job_id}/log")
def job_log(job_id: str) -> PlainTextResponse:
    if manager.get(job_id) is None:
        raise HTTPException(404, "job não encontrado")
    text = manager.read_log(job_id)
    if text is None:
        raise HTTPException(404, "log ainda não disponível")
    return PlainTextResponse(text)


@router.get("/jobs/{job_id}/pdf")
def job_pdf(job_id: str, inline: int = 0) -> FileResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "job não encontrado")
    pdf = manager.pdf_path(job_id)
    if pdf is None:
        raise HTTPException(404, "PDF não disponível para este job")
    stem = Path(job.main_tex).stem if job.main_tex else "output"
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{stem}.pdf"'},
    )


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> Response:
    try:
        manager.delete(job_id)
    except KeyError:
        raise HTTPException(404, "job não encontrado") from None
    except JobBusyError:
        raise HTTPException(409, "job em execução não pode ser removido") from None
    return Response(status_code=204)

"""Modelos de domínio do job de compilação."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    success = "success"
    error = "error"
    timeout = "timeout"


class Job(BaseModel):
    id: str
    state: JobState = JobState.queued
    source: str  # "upload" | "url"
    client_id: Optional[str] = None  # identifica o navegador que criou o job
    source_url: Optional[str] = None
    original_filename: Optional[str] = None
    main_tex: Optional[str] = None
    main_tex_candidates: list[str] = []
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_s: Optional[float] = None
    returncode: Optional[int] = None
    timeout: bool = False
    error: Optional[str] = None
    pdf_size: Optional[int] = None


class UrlJobRequest(BaseModel):
    url: str
    main_tex: Optional[str] = None
    client_id: Optional[str] = None

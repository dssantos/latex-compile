"""Download de fontes por URL: zip direto, GitHub (zipball), com limite de tamanho."""

import re
from pathlib import Path

import httpx

from .config import GITHUB_TOKEN, MAX_DOWNLOAD_MB

GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([^/]+))?/?$"
)

UA = "latex-compile/1.0"
MAX_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024


class DownloadError(Exception):
    """Falha de download (rede, HTTP, tamanho)."""


def resolve_url(url: str) -> tuple[str, dict[str, str]]:
    """Mapeia URLs de repo para o zipball correspondente e devolve headers."""
    url = url.strip()
    m = GITHUB_RE.match(url)
    if m:
        owner, repo, ref = m.groups()
        api = f"https://api.github.com/repos/{owner}/{repo}/zipball"
        if ref:
            api += f"/{ref}"
        headers = {"Accept": "application/vnd.github+zip"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        return api, headers
    return url, {}


def download(url: str, dest: Path) -> None:
    """Baixa url para dest em streaming, abortando acima de MAX_DOWNLOAD_MB."""
    resolved, headers = resolve_url(url)
    headers = {"User-Agent": UA, **headers}
    try:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(10.0, read=120.0)) as client:
            with client.stream("GET", resolved, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise DownloadError(f"HTTP {resp.status_code} ao baixar {url}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                    raise DownloadError(f"download excede o limite de {MAX_DOWNLOAD_MB} MB")
                total = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(256 * 1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise DownloadError(f"download excede o limite de {MAX_DOWNLOAD_MB} MB")
                        fh.write(chunk)
    except httpx.HTTPError as exc:
        raise DownloadError(f"falha de rede ao baixar {url}: {exc}") from exc

    if total == 0:
        raise DownloadError("resposta vazia ao baixar a URL")

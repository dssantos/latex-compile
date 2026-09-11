"""Extração segura de ZIP (anti zip-slip / zip bomb, nomes UTF-8, flatten de raiz)."""

import posixpath
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .config import MAX_EXTRACT_MB

SKIP_DIR_NAMES = {"__macosx"}  # comparado em lowercase
SKIP_FILE_NAMES = {".ds_store"}

# cota de segurança: soma dos tamanhos descomprimidos (e teto por arquivo)
MAX_TOTAL_BYTES = MAX_EXTRACT_MB * 1024 * 1024
MAX_SINGLE_BYTES = MAX_TOTAL_BYTES
SUSPICIOUS_RATIO = 100


class ZipError(Exception):
    """Zip inválido ou com conteúdo não permitido."""


def decode_name(info: zipfile.ZipInfo) -> str:
    """Decodifica o nome do entry respeitando o flag UTF-8 do formato ZIP.

    Sem o bit 11 (0x800), o nome foi codificado como cp437 pela spec — zips do
    Windows gravados com UTF-8 chegam corrompidos ("1. Introdu‡Æo.tex"); tentamos
    redecodificar cp437 -> utf-8.
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # modo unix nos 16 bits altos de external_attr
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _clean_parts(raw_name: str) -> list[str] | None:
    """Normaliza o nome em partes seguras; None se o entry deve ser ignorado."""
    name = raw_name.replace("\\", "/")
    if name.startswith("/"):
        raise ZipError(f"caminho absoluto não permitido no zip: {raw_name!r}")
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        raise ZipError(f"caminho inválido no zip (..): {raw_name!r}")
    if parts[0].lower() in SKIP_DIR_NAMES or parts[-1].lower() in SKIP_FILE_NAMES:
        return None
    return parts


def plan_entries(zip_path: Path) -> list[tuple[zipfile.ZipInfo, list[str]]]:
    """Valida todos os entries antes de escrever qualquer coisa no disco."""
    planned: list[tuple[zipfile.ZipInfo, list[str]]] = []
    links: list[tuple[zipfile.ZipInfo, list[str]]] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise ZipError("zip criptografado não é suportado")
            parts = _clean_parts(decode_name(info))
            if parts is None:
                continue
            if _is_symlink(info):
                # resolvido depois como cópia do alvo interno (nunca cria symlink)
                links.append((info, parts))
                continue
            if info.file_size > MAX_SINGLE_BYTES:
                raise ZipError(f"arquivo descomprimido grande demais: {info.filename!r}")
            if (
                info.compress_size > 0
                and info.file_size > 10 * 1024 * 1024
                and info.file_size / info.compress_size > SUSPICIOUS_RATIO
            ):
                raise ZipError(f"razão de compressão suspeita (zip bomb?): {info.filename!r}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ZipError(f"conteúdo do zip excede {MAX_EXTRACT_MB} MB descomprimidos")
            planned.append((info, parts))

    if not planned:
        raise ZipError("zip vazio ou sem arquivos utilizáveis")

    # Achata raiz única (ex.: zipball do GitHub vem como repo-<sha>/...)
    firsts = {parts[0] for _, parts in planned}
    if len(firsts) == 1 and all(len(parts) >= 2 for _, parts in planned):
        root = next(iter(firsts))
        planned = [(info, parts[1:]) for info, parts in planned]
        links = [(info, parts[1:] if parts[0] == root and len(parts) >= 2 else parts)
                 for info, parts in links]
    return planned, links


def safe_extract(zip_path: Path, dest: Path) -> list[str]:
    """Extrai zip_path em dest com segurança. Retorna os caminhos relativos criados."""
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    extracted: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        planned, links = plan_entries(zip_path)
        for info, parts in planned:
            target = dest_resolved.joinpath(*parts)
            # rede de segurança extra contra escape do diretório
            if not target.resolve().is_relative_to(dest_resolved):
                raise ZipError(f"caminho escapa do destino: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 256)
            extracted.append("/".join(parts))

        # Symlinks viram cópias do arquivo interno apontado (padrão em zipballs do
        # GitHub, ex.: examples/x.cls -> ../x.cls). Alvo absoluto, externo ou de
        # diretório é simplesmente ignorado — nunca criamos um symlink real.
        extracted_set = set(extracted)
        for info, parts in links:
            try:
                with zf.open(info) as src:
                    target_ref = src.read(4096).decode("utf-8", "replace").strip()
            except (OSError, RuntimeError, zipfile.BadZipFile):
                continue
            if not target_ref or target_ref.startswith("/"):
                continue
            link_rel = "/".join(parts)
            base = posixpath.dirname(link_rel)
            resolved = posixpath.normpath(posixpath.join(base, target_ref))
            if resolved.startswith("..") or resolved not in extracted_set:
                continue
            src_file = dest_resolved.joinpath(*resolved.split("/"))
            dst_file = dest_resolved.joinpath(*parts)
            if src_file.is_file():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_file, dst_file)
                extracted.append(link_rel)
    return extracted


def list_names(zip_path: Path) -> list[str]:
    """Nomes decodificados dos entries (validação rápida de upload)."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = _clean_parts(decode_name(info))
                if parts:
                    names.append("/".join(parts))
            return names
    except zipfile.BadZipFile:
        raise

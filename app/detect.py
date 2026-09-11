"""Detecção do arquivo .tex principal (o que contém \\documentclass)."""

import re
from pathlib import Path

DOCCLASS_RE = re.compile(rb"^\s*\\documentclass\b", re.MULTILINE)
READ_LIMIT = 256 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv"}


def find_main_tex(workdir: Path, override: str | None = None) -> tuple[str | None, list[str]]:
    """Retorna (main_tex_relpath | None, candidatos).

    Com override: usa o arquivo indicado se existir dentro de workdir; caso
    contrário devolve None junto com os candidatos encontrados (p/ diagnóstico).
    """
    candidates = _scan(workdir)
    if override:
        norm = override.strip().strip("/")
        target = (workdir / norm).resolve()
        if target.is_file() and target.is_relative_to(workdir.resolve()):
            return target.relative_to(workdir.resolve()).as_posix(), candidates
        return None, candidates
    return (candidates[0] if candidates else None), candidates


def _scan(workdir: Path) -> list[str]:
    root = workdir.resolve()
    found: list[str] = []
    for p in sorted(root.rglob("*.tex")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            with open(p, "rb") as fh:
                head = fh.read(READ_LIMIT)
        except OSError:
            continue
        if DOCCLASS_RE.search(head):
            found.append(rel.as_posix())

    def sort_key(rel: str) -> tuple:
        depth = rel.count("/")
        is_main = Path(rel).stem.lower() == "main"
        return (depth, 0 if is_main else 1, rel)

    found.sort(key=sort_key)
    return found

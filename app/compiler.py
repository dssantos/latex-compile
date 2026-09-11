"""Invocação do latexmk (pdflatex + bibtex) com timeout e captura de log."""

import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import COMPILE_TIMEOUT_S

# mensagens acionáveis para arquivos de pacote ausentes (mapa -> pacote Debian)
PKG_HINTS = {
    "abntex2.sty": "texlive-publishers",
    "abntex2cite.sty": "texlive-publishers",
    "abntex2-abnt.bst": "texlive-publishers",
    "abntex2-alf.bst": "texlive-publishers",
    "abntex2-options.bib": "texlive-publishers",
    "subfigure.sty": "texlive-latex-extra",
    "hyphenat.sty": "texlive-latex-extra",
    "xfrac.sty": "texlive-latex-recommended",
    "acronym.sty": "texlive-latex-extra",
    "pifont.sty": "texlive-latex-recommended",
    "lmodern.sty": "lmodern",
}


@dataclass
class CompileResult:
    returncode: int
    timed_out: bool = False
    pdf_path: Path | None = None
    error: str | None = None


def _friendly_error(log_text: str) -> str | None:
    m = re.search(r"! LaTeX Error: File `(.+?)' not found", log_text)
    if m:
        fn = m.group(1)
        hint = PKG_HINTS.get(fn.lower())
        if hint:
            return f"Arquivo ausente na imagem: `{fn}`. Adicione o pacote Debian '{hint}' ao Dockerfile e faça rebuild."
        return f"Arquivo não encontrado: `{fn}`. Verifique se ele está no zip do projeto."
    if re.search(r"fontspec package requires either XeTeX or LuaTeX", log_text):
        return (
            "Este projeto usa fontspec e exige compilação com XeTeX ou LuaTeX; "
            "o serviço compila com pdflatex. Recrie o projeto em pdflatex (ex.: trocando "
            "fontspec/inputenc) para compilá-lo aqui."
        )
    # estilo -file-line-error: ./arquivo.tex:123: mensagem (tex, sty ou cls)
    m = re.search(r"^(\S+?\.(?:tex|sty|cls)):(\d+): (.+)$", log_text, re.MULTILINE)
    if m:
        return f"{m.group(1)}:{m.group(2)}: {m.group(3)}"
    m = re.search(r"^! (.+)$", log_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def run_latexmk(
    workdir: Path,
    main_tex: str,
    log_path: Path,
    timeout_s: int = COMPILE_TIMEOUT_S,
) -> CompileResult:
    """Roda latexmk em workdir. Log completo vai para log_path.

    start_new_session=True cria um novo grupo de processos; no timeout matamos
    o grupo inteiro (latexmk + pdflatex + bibtex filhos) com SIGKILL.
    """
    home = tempfile.mkdtemp(prefix="latex-home-")  # latexmk/fontconfig precisam de HOME gravável
    env = os.environ.copy()
    env.update(
        HOME=home,
        LANG="C.UTF-8",
        LC_ALL="C.UTF-8",
        max_print_line="10000",  # evita quebra artificial de linhas no log
    )
    cmd = [
        "latexmk",
        "-pdf",                  # engine pdflatex
        "-bibtex",               # BibTeX clássico (nunca biber)
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        main_tex,
    ]
    timed_out = False
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "wb") as log_fh:
            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                rc = proc.wait()
    finally:
        shutil.rmtree(home, ignore_errors=True)

    pdf_rel = Path(main_tex).with_suffix(".pdf")
    pdf_path = workdir / pdf_rel
    if timed_out:
        return CompileResult(returncode=rc, timed_out=True, error=f"compilação excedeu {timeout_s}s")
    if rc == 0 and pdf_path.is_file():
        return CompileResult(returncode=rc, pdf_path=pdf_path)

    log_text = ""
    try:
        log_text = log_path.read_text(errors="replace")
    except OSError:
        pass
    error = _friendly_error(log_text) or f"latexmk retornou código {rc}"
    return CompileResult(returncode=rc, error=error)

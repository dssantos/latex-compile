FROM python:3.12-slim

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PIP_NO_CACHE_DIR=1

# texlive seletivo: abntex2 vive em texlive-publishers; --no-install-recommends
# evita texlive-fonts-extra (~1,5 GB). Imagem final ~2 GB.
RUN apt-get update && apt-get install -y --no-install-recommends \
      latexmk curl ca-certificates \
      texlive-latex-base texlive-latex-recommended texlive-latex-extra \
      texlive-fonts-recommended texlive-publishers texlive-lang-portuguese \
      lmodern \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 latex

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY static/ static/
RUN mkdir -p /app/jobs && chown latex:latex /app/jobs

USER latex
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

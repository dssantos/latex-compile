# Compilador LaTeX

Serviço web que compila projetos LaTeX e gera o PDF, a partir de um **upload de .zip** ou de um **link** (zip direto ou repositório GitHub). Roda em Docker Compose com TeXLive seletivo (pdflatex + BibTeX).

## Por que este projeto existe

Este compilador nasceu de uma necessidade real: compilar um documento acadêmico de ~70 páginas, com dezenas de imagens e bibliografia ABNT, que **estourava o limite de tempo de compilação da conta gratuita** das plataformas online de LaTeX. A saída foi trazer a compilação para a própria máquina, via Docker — sem limites de tempo, sem plano pago e usando o poder computacional do próprio computador.

Se você já bateu nesse mesmo limite, este projeto é para você.

## Uso rápido

```bash
mkdir -p jobs          # dono uid 1000 (o mesmo do usuário no container)
docker compose up --build -d
```

Abra **http://localhost:8000**:

1. Arraste um `.zip` do projeto LaTeX (ou clique na área de upload) — ou cole uma URL e clique em "Compilar de link".
2. Acompanhe o estado na tabela (Na fila → Compilando → Concluído).
3. Clique em **Log** para ver a saída do latexmk ao vivo e **PDF** para pré-visualizar/baixar o resultado.

### Testar

```bash
curl -s -F "file=@caminho/para/meu-projeto.zip" \
     http://localhost:8000/api/jobs/upload
```

Ou pela interface web, arrastando o arquivo .zip do projeto.

### Formatos de link aceitos

- Repositório GitHub: `https://github.com/usuario/repo` (branch padrão) ou `.../tree/branch`
- Qualquer URL direta que devolva um `.zip`

## API

| Endpoint | Descrição |
|---|---|
| `GET /api/health` | Healthcheck |
| `POST /api/jobs/upload` | multipart: `file` (.zip) + `main_tex` opcional → `202` |
| `POST /api/jobs/url` | JSON `{"url", "main_tex"?}` → `202` |
| `GET /api/jobs` | Lista de jobs (mais recentes primeiro) |
| `GET /api/jobs/{id}?tail=200` | Detalhe + últimas linhas do log |
| `GET /api/jobs/{id}/log` | Log completo (text/plain) |
| `GET /api/jobs/{id}/pdf?inline=1` | PDF gerado (`inline=1` para preview) |
| `DELETE /api/jobs/{id}` | Remove o job (`409` se em execução) |

## Configuração (env no docker-compose.yml)

| Variável | Padrão | Descrição |
|---|---|---|
| `MAX_UPLOAD_MB` | 100 | Tamanho máximo do zip enviado |
| `MAX_DOWNLOAD_MB` | 200 | Tamanho máximo do download por URL |
| `MAX_EXTRACT_MB` | 500 | Cota descomprimida (anti zip bomb) |
| `COMPILE_TIMEOUT_S` | 300 | Timeout da compilação |
| `MAX_WORKERS` | 2 | Compilações simultâneas |
| `KEEP_WORK` | false | Manter o diretório de build após o fim do job (debug) |
| `JOBS_QUOTA_MB` | 1000 | Cota total do diretório `jobs/`; excedeu → apaga os jobs concluídos mais antigos (0 = sem limite) |
| `JOBS_MAX_AGE_H` | 0 | Expira jobs com mais de N horas (0 = nunca) |
| `GITHUB_TOKEN` | — | Token GitHub opcional (rate limit / repos privados) |

## Notas

- **Compilação**: `latexmk -pdf -bibtex -interaction=nonstopmode -halt-on-error -file-line-error` — pdflatex + BibTeX clássico, com rerun automático das passadas. `-shell-escape` nunca é habilitado.
- **Arquivo principal**: detectado automaticamente (o `.tex` mais raso com `\documentclass`, preferência por `main.tex`); dá para forçar com o campo "Arquivo principal".
- **Persistência**: cada job fica em `jobs/{id}/` (`meta.json`, `source.zip`, `build.log`, `output.pdf`), sobrevivendo a reinícios do container. O diretório intermediário `work/` é apagado ao fim do job, e a cota `JOBS_QUOTA_MB` expulsa os jobs concluídos mais antigos quando o total passa do limite.
- **Pacotes TeXLive**: a imagem instala `texlive-latex-extra`, `texlive-publishers` (abntex2), `texlive-lang-portuguese`, entre outros (~2 GB). Se um projeto pedir outro pacote, adicione o pacote Debian ao [Dockerfile](Dockerfile) e rode `docker compose up --build -d`.
- **Disco**: jobs concluídos guardam zip + log + PDF; o diretório de trabalho intermediário é apagado. `docker builder prune` devolve espaço do cache de build.

## Sugestões e contribuições

Este é um projeto aberto — ideias são bem-vindas! Se tiver uma sugestão de melhoria, encontrou um bug ou quer uma funcionalidade nova (outros engines, mais provedores de link, interface em outros idiomas…), abra uma [issue](../../issues). Pull requests também são recebidos com prazer.

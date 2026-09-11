"use strict";

const $ = (sel) => document.querySelector(sel);

// Identidade deste navegador: todos os jobs criados aqui recebem este ID e a
// lista só exibe os dele. Persistido no localStorage (sobrevive a recargas).
function makeClientId() {
  if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}
const CLIENT_ID = (() => {
  const KEY = "latex_compile_client_id";
  let id = localStorage.getItem(KEY);
  if (!id) {
    id = makeClientId();
    localStorage.setItem(KEY, id);
  }
  return id;
})();

const STATE_LABEL = {
  queued: "Na fila",
  running: "Compilando",
  success: "Concluído",
  error: "Erro",
  timeout: "Tempo esgotado",
};

const jobsCache = new Map();
let logJobId = null;
let pollTimer = null;
let pollInFlight = false;

// ---------- utilitários ----------

async function fetchJSON(url, options) {
  const resp = await fetch(url, options);
  let data = null;
  if (resp.status !== 204) {
    data = await resp.json().catch(() => null); // corpo lido uma única vez
  }
  if (!resp.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${resp.status}`;
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return data;
}

function fmtDuration(s) {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function fmtSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtCreatedAt(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtBytes(n) {
  if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (n >= 1024 * 1024) return `${Math.round(n / (1024 * 1024))} MB`;
  return `${Math.max(1, Math.round(n / 1024))} kB`;
}

function showFormError(msg) {
  const el = $("#formError");
  el.textContent = msg;
  el.hidden = !msg;
}

// ---------- tabela ----------

function renderTable() {
  const tbody = $("#jobsBody");
  tbody.textContent = "";
  const jobs = [...jobsCache.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  $("#emptyMsg").hidden = jobs.length > 0;

  for (const job of jobs) {
    const tr = document.createElement("tr");

    const tdId = document.createElement("td");
    tdId.className = "mono";
    tdId.textContent = job.id.slice(0, 8);
    tdId.title = job.id;
    tr.appendChild(tdId);

    const tdSrc = document.createElement("td");
    tdSrc.className = "src";
    tdSrc.textContent = job.source === "url"
      ? (job.source_url || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 34)
      : job.original_filename || "upload";
    tdSrc.title = job.source === "url" ? job.source_url : job.original_filename;
    tr.appendChild(tdSrc);

    const tdState = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `badge st-${job.state}`;
    badge.textContent = STATE_LABEL[job.state] || job.state;
    tdState.appendChild(badge);
    if (job.error) {
      tdState.title = job.error;
      tdState.appendChild(Object.assign(document.createElement("div"), {
        className: "err-hint", textContent: job.error.length > 90 ? job.error.slice(0, 90) + "…" : job.error,
      }));
    }
    tr.appendChild(tdState);

    const tdCreated = document.createElement("td");
    tdCreated.textContent = fmtCreatedAt(job.created_at);
    tdCreated.title = new Date(job.created_at).toLocaleString("pt-BR");
    tr.appendChild(tdCreated);

    const tdMain = document.createElement("td");
    tdMain.className = "mono";
    tdMain.textContent = job.main_tex || "—";
    tr.appendChild(tdMain);

    const tdDur = document.createElement("td");
    tdDur.textContent = fmtDuration(job.duration_s);
    tr.appendChild(tdDur);

    const tdAct = document.createElement("td");
    tdAct.className = "actions";

    tdAct.appendChild(actionBtn("Log", () => openLog(job.id), false));
    tdAct.appendChild(actionBtn("PDF", () => openPdf(job.id), job.state !== "success"));
    tdAct.appendChild(actionBtn("Excluir", () => deleteJob(job.id),
      job.state === "queued" || job.state === "running"));

    tr.appendChild(tdAct);
    tbody.appendChild(tr);
  }
}

function actionBtn(label, onClick, disabled) {
  const b = document.createElement("button");
  b.className = "link-btn";
  b.textContent = label;
  b.disabled = disabled;
  b.addEventListener("click", onClick);
  return b;
}

// ---------- ações ----------

function mainTexValue() {
  return $("#mainTex").value.trim();
}

async function submitUpload(file) {
  showFormError("");
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".zip")) {
    showFormError("O arquivo precisa ser um .zip");
    return;
  }
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("main_tex", mainTexValue());
  fd.append("client_id", CLIENT_ID);
  try {
    const job = await fetchJSON("/api/jobs/upload", { method: "POST", body: fd });
    jobsCache.set(job.id, job);
    renderTable();
    pollSoon();
  } catch (err) {
    showFormError(`Falha no envio: ${err.message}`);
  }
}

async function submitUrl(event) {
  event.preventDefault();
  showFormError("");
  const url = $("#urlInput").value.trim();
  if (!url) return;
  $("#urlBtn").disabled = true;
  try {
    const job = await fetchJSON("/api/jobs/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, main_tex: mainTexValue() || null, client_id: CLIENT_ID }),
    });
    jobsCache.set(job.id, job);
    $("#urlInput").value = "";
    renderTable();
    pollSoon();
  } catch (err) {
    showFormError(`Falha ao criar trabalho: ${err.message}`);
  } finally {
    $("#urlBtn").disabled = false;
  }
}

async function deleteJob(id) {
  if (!confirm("Remover este trabalho e seus arquivos?")) return;
  try {
    await fetchJSON(`/api/jobs/${id}`, { method: "DELETE" });
    jobsCache.delete(id);
    renderTable();
  } catch (err) {
    alert(err.status === 409 ? "O job está em execução e não pode ser removido." : err.message);
  }
}

// ---------- modais ----------

function closeModal(modal) {
  modal.hidden = true;
  if (modal.id === "pdfModal") $("#pdfFrame").src = "about:blank";
  if (modal.id === "logModal") logJobId = null;
}

function wireModals() {
  for (const modal of document.querySelectorAll(".modal")) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal || e.target.closest("[data-close]")) closeModal(modal);
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
  });
}

async function openLog(id) {
  logJobId = id;
  const modal = $("#logModal");
  modal.hidden = false;
  $("#logDownload").href = `/api/jobs/${id}/log`;
  await refreshLog(true);
}

async function refreshLog(force) {
  if (!logJobId || $("#logModal").hidden) return;
  const id = logJobId;
  try {
    const detail = await fetchJSON(`/api/jobs/${id}?tail=400`);
    if (logJobId !== id) return;
    jobsCache.set(id, detail);
    renderTable();
    $("#logTitle").textContent = `Log ${id.slice(0, 8)} — ${STATE_LABEL[detail.state] || detail.state}`;
    const pre = $("#logContent");
    const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 60;
    if (detail.log_tail && (detail.log_tail !== pre.dataset.content || force)) {
      pre.dataset.content = detail.log_tail;
      pre.textContent = detail.log_tail;
      if (nearBottom || force) pre.scrollTop = pre.scrollHeight;
    }
    if (detail.error) {
      pre.textContent += `\n\n=== ERRO ===\n${detail.error}`;
    }
  } catch { /* log pode não existir ainda */ }
}

function openPdf(id) {
  $("#pdfModal").hidden = false;
  $("#pdfTitle").textContent = `PDF ${id.slice(0, 8)}`;
  $("#pdfOpen").href = `/api/jobs/${id}/pdf`;
  $("#pdfFrame").src = `/api/jobs/${id}/pdf?inline=1`;
}

// ---------- polling ----------

function hasActive() {
  return [...jobsCache.values()].some((j) => j.state === "queued" || j.state === "running");
}

async function refreshStorage() {
  try {
    const st = await fetchJSON("/api/storage");
    const el = $("#storageInfo");
    if (st.quota_bytes > 0) {
      el.textContent = `Armazenamento: ${fmtBytes(st.used_bytes)} de ${fmtBytes(st.quota_bytes)} em uso` +
        (st.max_age_h > 0 ? ` · jobs expiram em ${st.max_age_h}h` : "");
    } else {
      el.textContent = `Armazenamento: ${fmtBytes(st.used_bytes)} em uso (sem cota)`;
    }
  } catch { /* indicador é best-effort */ }
}

async function poll() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const jobs = await fetchJSON(`/api/jobs?client_id=${encodeURIComponent(CLIENT_ID)}`);
    for (const job of jobs) jobsCache.set(job.id, job);
    renderTable();
    refreshStorage();
    if (logJobId && hasActive()) refreshLog(false);
    $("#serverStatus").textContent = "serviço ok";
  } catch {
    $("#serverStatus").textContent = "serviço indisponível — tentando novamente…";
  } finally {
    pollInFlight = false;
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, hasActive() ? 2000 : 10000);
  }
}

function pollSoon() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, 400);
}

// ---------- wire up ----------

function init() {
  const dz = $("#dropzone");
  const fileInput = $("#fileInput");
  dz.addEventListener("click", () => fileInput.click());
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fileInput.click(); });
  fileInput.addEventListener("change", () => {
    submitUpload(fileInput.files[0]);
    fileInput.value = "";
  });
  for (const ev of ["dragover", "dragenter"]) {
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); });
  }
  for (const ev of ["dragleave", "drop"]) {
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); });
  }
  dz.addEventListener("drop", (e) => submitUpload(e.dataTransfer.files[0]));
  // soltar o zip em qualquer lugar da página também funciona
  document.addEventListener("drop", (e) => {
    if (e.defaultPrevented) return;
    e.preventDefault();
    submitUpload(e.dataTransfer.files[0]);
  });
  document.addEventListener("dragover", (e) => e.preventDefault());

  $("#urlForm").addEventListener("submit", submitUrl);
  wireModals();
  poll();
}

init();

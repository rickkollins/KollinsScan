"use strict";

const $ = (sel) => document.querySelector(sel);
let me = null;

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.method && opts.method !== "GET") {
    opts.headers = { "X-KollinsScan": "1", ...(opts.headers || {}) };
  }
  const resp = await fetch(path, opts);
  if (resp.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Not signed in");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

// ---- sign in ------------------------------------------------------------

function showLogin() {
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
  $("#login-form input[name=user]").focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const err = $("#login-error");
  err.hidden = true;
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: form.get("user"), password: form.get("password") }),
    });
    e.target.reset();
    await start();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

async function start() {
  try {
    me = await api("/api/me");
  } catch {
    return; // showLogin() already ran
  }
  const select = $("#language");
  select.replaceChildren();
  const saved = localStorageGet("language");
  for (const lang of me.languages) {
    select.append(new Option(lang, lang));
  }
  select.value = me.languages.includes(saved) ? saved : me.default_language;
  $("#limits").textContent =
    `JPG, PNG, TIFF, BMP, GIF or WebP, up to ${me.max_upload_mb} MB each`;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  loadHistory();
}

$("#language").addEventListener("change", (e) => localStorageSet("language", e.target.value));

function localStorageGet(key) {
  try { return localStorage.getItem(`kollinsscan.${key}`); } catch { return null; }
}
function localStorageSet(key, value) {
  try { localStorage.setItem(`kollinsscan.${key}`, value); } catch { /* private mode */ }
}

// ---- uploads ------------------------------------------------------------

const dropzone = $("#dropzone");
const fileInput = $("#file-input");

fileInput.addEventListener("change", () => {
  handleFiles(fileInput.files);
  fileInput.value = "";
});
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("over"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("over");
  handleFiles(e.dataTransfer.files);
});
document.addEventListener("paste", (e) => {
  if ($("#app-view").hidden) return;
  const files = [...e.clipboardData.items]
    .filter((i) => i.kind === "file")
    .map((i) => i.getAsFile())
    .filter(Boolean);
  if (files.length) handleFiles(files);
});

async function handleFiles(fileList) {
  const files = [...fileList];
  // Make all the cards first so the user sees everything queued, then
  // read them one at a time (the server limits concurrent OCR anyway).
  const jobs = files.map((file) => ({ file, card: newCard(file.name || "pasted image") }));
  $("#results").prepend(...jobs.map(({ card }) => card.root));
  for (const { file, card } of jobs) {
    card.preview(file);
    await readFile(file, card);
  }
}

async function readFile(file, card) {
  if (file.size > me.max_upload_mb * 1024 * 1024) {
    card.fail(`Too large: images must be under ${me.max_upload_mb} MB.`);
    return;
  }
  card.busy("Reading text…");
  const body = new FormData();
  body.append("file", file, file.name || "pasted.png");
  body.append("language", $("#language").value);
  try {
    const result = await api("/api/ocr", { method: "POST", body });
    card.show(result);
    loadHistory();
  } catch (ex) {
    card.fail(ex.message);
  }
}

// ---- result cards -------------------------------------------------------

function newCard(name) {
  const root = $("#result-template").content.firstElementChild.cloneNode(true);
  const text = root.querySelector(".text");
  const meta = root.querySelector(".meta");
  const download = root.querySelector(".download");
  const img = root.querySelector(".preview");
  root.querySelector(".filename").textContent = name;
  download.hidden = true;
  root.querySelector(".copy").hidden = true;
  root.querySelector(".result-body").classList.add("no-preview");

  root.querySelector(".copy").addEventListener("click", async (e) => {
    try {
      await navigator.clipboard.writeText(text.value);
    } catch {
      text.select();
      document.execCommand("copy");
    }
    e.target.textContent = "Copied";
    setTimeout(() => (e.target.textContent = "Copy"), 1500);
  });
  root.querySelector(".close").addEventListener("click", () => {
    if (img.src) URL.revokeObjectURL(img.src);
    root.remove();
  });

  return {
    root,
    preview(file) {
      img.src = URL.createObjectURL(file);
      img.hidden = false;
      img.onerror = () => {  // e.g. TIFF, which most browsers can't show
        img.hidden = true;
        root.querySelector(".result-body").classList.add("no-preview");
      };
      root.querySelector(".result-body").classList.remove("no-preview");
    },
    busy(message) {
      root.classList.add("busy");
      meta.textContent = message;
    },
    fail(message) {
      root.classList.remove("busy");
      root.classList.add("failed");
      meta.textContent = message;
    },
    show(result) {
      root.classList.remove("busy", "failed");
      root.dataset.id = result.id;
      root.querySelector(".filename").textContent = result.filename;
      const pages = result.pages > 1 ? ` · ${result.pages} pages` : "";
      const words = result.text.split(/\s+/).filter(Boolean).length;
      meta.textContent = `${words} words · ${result.language}${pages} · ${result.seconds}s`;
      text.value = result.text || "(No text found in this image.)";
      download.href = `/api/results/${result.id}/txt`;
      download.hidden = false;
      root.querySelector(".copy").hidden = !result.text;
    },
  };
}

// ---- history ------------------------------------------------------------

async function loadHistory() {
  let items;
  try {
    items = await api("/api/results");
  } catch {
    return;
  }
  const list = $("#history-list");
  list.replaceChildren();
  $("#history-empty").hidden = items.length > 0;
  for (const item of items) {
    const li = document.createElement("li");
    const open = document.createElement("button");
    open.className = "open";
    const name = document.createElement("span");
    name.textContent = item.filename;
    const when = document.createElement("span");
    when.className = "muted small";
    when.textContent = new Date(item.created * 1000).toLocaleString();
    open.append(name, when);
    open.title = item.preview;
    open.addEventListener("click", () => openResult(item.id));

    const del = document.createElement("button");
    del.className = "delete ghost";
    del.textContent = "✕";
    del.title = "Delete";
    del.addEventListener("click", async () => {
      if (!confirm(`Delete the text from ${item.filename}?`)) return;
      await api(`/api/results/${item.id}`, { method: "DELETE" }).catch((ex) => alert(ex.message));
      document.querySelectorAll(`.result[data-id="${item.id}"]`).forEach((el) => el.remove());
      loadHistory();
    });
    li.append(open, del);
    list.append(li);
  }
}

async function openResult(id) {
  const existing = document.querySelector(`.result[data-id="${id}"]`);
  if (existing) {
    existing.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  try {
    const result = await api(`/api/results/${id}`);
    const card = newCard(result.filename);
    card.show(result);
    $("#results").prepend(card.root);
    card.root.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (ex) {
    alert(ex.message);
  }
}

start();

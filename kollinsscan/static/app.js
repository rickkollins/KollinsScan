"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
let me = null;       // /api/me
let book = null;     // the open book

// ==========================================================================
// Server
// ==========================================================================

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.json !== undefined) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    delete opts.json;
  }
  if (opts.method && opts.method !== "GET") {
    opts.headers = { "X-KollinsScan": "1", ...(opts.headers || {}) };
  }
  const resp = await fetch(path, opts);
  if (resp.status === 401 && path !== "/api/login") {
    show("login");
    throw new Error("Not signed in");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`kollinsscan.${key}`);
    localStorage.setItem(`kollinsscan.${key}`, value);
  } catch { /* private browsing */ }
  return null;
}

function show(view) {
  for (const v of ["login", "library", "book"]) $(`#${v}-view`).hidden = v !== view;
  if (view !== "book") stopCamera();
}

// ==========================================================================
// Sign in and routing
// ==========================================================================

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const err = $("#login-error");
  err.hidden = true;
  try {
    await api("/api/login", { method: "POST", json: { user: form.get("user"), password: form.get("password") } });
    e.target.reset();
    await start();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

for (const btn of $$(".logout")) {
  btn.addEventListener("click", async () => {
    if (!(await saveAll())) return;
    await api("/api/logout", { method: "POST" }).catch(() => {});
    show("login");
  });
}

async function start() {
  try {
    me = await api("/api/me");
  } catch {
    if (!$("#login-view").hidden) $("#login-form input[name=user]").focus();
    return;
  }
  for (const select of $$(".language-select")) {
    select.replaceChildren(...me.languages.map((l) => new Option(l, l)));
    select.value = me.default_language;
  }
  route();
}

window.addEventListener("hashchange", async () => {
  if (book && !(await saveAll())) return;
  route();
});

function route() {
  const m = location.hash.match(/^#\/book\/(\d+)/);
  if (m) openBook(Number(m[1]));
  else openLibrary();
}

window.addEventListener("beforeunload", (e) => {
  if (dirty.size || scanQueue.length) e.preventDefault();
});

// ==========================================================================
// Library
// ==========================================================================

async function openLibrary() {
  book = null;
  show("library");
  const books = await api("/api/books").catch(() => []);
  $("#no-books").hidden = books.length > 0;
  $("#book-list").replaceChildren(...books.map((b) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `#/book/${b.id}`;
    const title = document.createElement("strong");
    title.textContent = b.title;
    const info = document.createElement("span");
    info.className = "muted small";
    info.textContent = [b.author, `${b.pages} page${b.pages === 1 ? "" : "s"}`,
      `edited ${new Date(b.updated * 1000).toLocaleDateString()}`].filter(Boolean).join(" · ");
    a.append(title, info);
    const del = document.createElement("button");
    del.className = "ghost";
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      if (!confirm(`Delete "${b.title}" and all ${b.pages} scanned pages? This can't be undone.`)) return;
      await api(`/api/books/${b.id}`, { method: "DELETE" }).catch((ex) => alert(ex.message));
      openLibrary();
    });
    li.append(a, del);
    return li;
  }));
}

$("#new-book").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const err = $("#new-book-error");
  err.hidden = true;
  try {
    const { id } = await api("/api/books", { method: "POST", json: Object.fromEntries(form) });
    e.target.reset();
    location.hash = `#/book/${id}`;
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

// ==========================================================================
// Book
// ==========================================================================

async function openBook(id) {
  try {
    book = await api(`/api/books/${id}`);
  } catch (ex) {
    alert(ex.message);
    location.hash = "#/";
    return;
  }
  show("book");
  document.title = `${book.title} · KollinsScan`;
  $("#book-title").value = book.title;
  $("#book-author").value = book.author;
  $("#export-rtf").href = `/api/books/${id}/export?format=rtf`;
  $("#export-txt").href = `/api/books/${id}/export?format=txt`;
  $("#grammar-off").hidden = me.grammar;
  $("#grammar-on").hidden = !me.grammar;
  $("#auto-check").parentElement.hidden = !me.grammar;
  issues = [];
  renderIssues();
  renderBookLists();
  $("#pages").replaceChildren(...book.pages.map((p) => pageElement(p)));
  updateEmpty();
  updateContents();
  setMode("append");
  suggestNextLabel();
  if (store("camera.on") === "1") startCamera();
}

async function saveBook(fields) {
  try {
    const updated = await api(`/api/books/${book.id}`, { method: "PATCH", json: fields });
    Object.assign(book, updated);
    renderBookLists();
  } catch (ex) {
    alert(ex.message);
  }
}

$("#book-title").addEventListener("change", (e) => {
  if (e.target.value.trim()) saveBook({ title: e.target.value });
  else e.target.value = book.title;
});
$("#book-author").addEventListener("change", (e) => saveBook({ author: e.target.value }));

for (const id of ["#export-rtf", "#export-txt"]) {
  $(id).addEventListener("click", async (e) => {
    if (!dirty.size) return;
    e.preventDefault();
    if (await saveAll()) location.href = e.currentTarget.href;
  });
}

function updateEmpty() {
  $("#no-pages").hidden = $("#pages").children.length > 0;
}

// ==========================================================================
// Pages
// ==========================================================================

function pageElement(page) {
  const el = $("#page-template").content.firstElementChild.cloneNode(true);
  el.dataset.id = page.id || "";
  const text = $(".page-text", el);
  const label = $(".label", el);
  text.innerHTML = page.html || "";  // already cleaned by the server
  label.value = page.label || "";

  text.addEventListener("input", () => {
    markDirty(el);
    scheduleContents();
  });
  text.addEventListener("focus", () => { currentPage = el; });
  text.addEventListener("paste", (e) => {
    // Paste as plain text so no outside formatting comes along.
    e.preventDefault();
    const pasted = e.clipboardData.getData("text/plain");
    document.execCommand("insertText", false, pasted);
  });
  label.addEventListener("change", () => {
    markDirty(el);
    updateContents();
    suggestNextLabel();
  });

  $(".photo", el).addEventListener("click", () => {
    const img = $(".page-photo", el);
    const on = !el.classList.contains("show-photo");
    el.classList.toggle("show-photo", on);
    img.hidden = !on;
    if (on && !img.src) {
      img.src = `/api/pages/${el.dataset.id}/image`;
      img.onerror = () => { img.hidden = true; img.replaceWith(Object.assign(document.createElement("p"), { className: "muted small", textContent: "No photo saved for this page." })); };
    }
  });
  $(".rescan", el).addEventListener("click", () => setMode("replace", el));
  $(".insert", el).addEventListener("click", () => setMode("after", el));
  $(".up", el).addEventListener("click", () => movePage(el, "up"));
  $(".down", el).addEventListener("click", () => movePage(el, "down"));
  $(".delete", el).addEventListener("click", async () => {
    if (!confirm(`Delete page ${label.value || ""}? Its text and photo are removed.`)) return;
    try {
      await api(`/api/pages/${el.dataset.id}`, { method: "DELETE" });
    } catch (ex) {
      alert(ex.message);
      return;
    }
    dirty.delete(el);
    dropIssues(el);
    el.remove();
    updateEmpty();
    updateContents();
    suggestNextLabel();
  });
  if (page.words !== undefined) {
    $(".page-info", el).textContent = `${page.words} words · read in ${page.seconds}s`;
  }
  return el;
}

async function movePage(el, direction) {
  if (!(await saveAll())) return;
  try {
    await api(`/api/pages/${el.dataset.id}/move`, { method: "POST", json: { direction } });
  } catch (ex) {
    alert(ex.message);
    return;
  }
  if (direction === "up" && el.previousElementSibling) el.previousElementSibling.before(el);
  if (direction === "down" && el.nextElementSibling) el.nextElementSibling.after(el);
  updateContents();
  el.scrollIntoView({ block: "nearest" });
}

// ---- autosave -------------------------------------------------------------

const dirty = new Set();
let saveTimer = null;
let currentPage = null;

function markDirty(el) {
  dirty.add(el);
  setSaveState("Unsaved changes");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveAll, 1200);
}

function setSaveState(text) {
  $("#save-state").textContent = text;
}

async function saveAll() {
  clearTimeout(saveTimer);
  if (!dirty.size) return true;
  setSaveState("Saving…");
  for (const el of [...dirty]) {
    if (!el.dataset.id) continue;  // still being scanned
    dirty.delete(el);
    try {
      await api(`/api/pages/${el.dataset.id}`, {
        method: "PATCH",
        json: { html: $(".page-text", el).innerHTML, label: $(".label", el).value },
      });
    } catch (ex) {
      dirty.add(el);
      setSaveState(`Not saved: ${ex.message}`);
      saveTimer = setTimeout(saveAll, 5000);
      return false;
    }
  }
  setSaveState(dirty.size ? "Unsaved changes" : "All changes saved");
  return true;
}

// ---- formatting toolbar -------------------------------------------------

document.execCommand("defaultParagraphSeparator", false, "p");

function editingPage() {
  const node = getSelection().anchorNode;
  const text = node && (node.nodeType === 1 ? node : node.parentElement)?.closest(".page-text");
  return text ? text.closest(".page") : null;
}

for (const btn of $$("#toolbar button")) {
  // mousedown + preventDefault keeps the text selection in the editor.
  btn.addEventListener("mousedown", (e) => e.preventDefault());
}
for (const btn of $$("#toolbar [data-block]")) {
  btn.addEventListener("click", () => {
    const page = editingPage();
    if (!page) return alert("Click in the text first.");
    document.execCommand("formatBlock", false, btn.dataset.block);
    markDirty(page);
    updateContents();
  });
}
for (const btn of $$("#toolbar [data-inline]")) {
  btn.addEventListener("click", () => {
    const page = editingPage();
    if (!page) return;
    document.execCommand(btn.dataset.inline);
    markDirty(page);
  });
}
$("#clear-marks").addEventListener("click", () => {
  const page = editingPage() || currentPage;
  if (!page) return alert("Click in a page first.");
  for (const mark of $$("mark", page)) mark.replaceWith(...mark.childNodes);
  $(".page-text", page).normalize();
  markDirty(page);
});

// ==========================================================================
// Camera and scanning
// ==========================================================================

const video = $("#video");
let stream = null;
let rotation = Number(store("camera.rotation") || 0);
$("#rotation").textContent = `${rotation}°`;

async function listCameras() {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const select = $("#camera-select");
  select.replaceChildren(...devices.map((d, n) => new Option(d.label || `Camera ${n + 1}`, d.deviceId)));
  const current = stream?.getVideoTracks()[0]?.getSettings().deviceId;
  if (current) select.value = current;
}

async function startCamera(deviceId = store("camera.device")) {
  const err = $("#camera-error");
  err.hidden = true;
  if (!navigator.mediaDevices?.getUserMedia) {
    err.textContent = "This browser can't use cameras here. Open the site over https://.";
    err.hidden = false;
    return;
  }
  stopCamera();
  // Ask for the camera's best resolution: more pixels per letter = better OCR.
  const video_ = { width: { ideal: 4096 }, height: { ideal: 2160 } };
  if (deviceId) video_.deviceId = { exact: deviceId };
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: video_, audio: false });
  } catch (ex) {
    if (deviceId && ex.name === "OverconstrainedError") return startCamera("");  // unplugged
    err.textContent = ex.name === "NotAllowedError"
      ? "Camera access was blocked. Allow it in the browser's address bar, then try again."
      : `Couldn't start the camera: ${ex.message || ex.name}`;
    err.hidden = false;
    return;
  }
  video.srcObject = stream;
  video.hidden = false;
  $("#camera-off").hidden = true;
  $("#capture").disabled = false;
  store("camera.on", "1");
  const settings = stream.getVideoTracks()[0].getSettings();
  store("camera.device", settings.deviceId || "");
  await listCameras();
  status(`Camera ready (${settings.width}×${settings.height}).`);
}

function stopCamera() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  video.hidden = true;
  $("#camera-off").hidden = false;
  $("#capture").disabled = true;
}

$("#start-camera").addEventListener("click", () => startCamera());
$("#camera-select").addEventListener("change", (e) => startCamera(e.target.value));
$("#rotate").addEventListener("click", () => {
  rotation = (rotation + 90) % 360;
  store("camera.rotation", String(rotation));
  $("#rotation").textContent = `${rotation}°`;
  video.style.transform = `rotate(${rotation}deg)`;
});
video.style.transform = `rotate(${rotation}deg)`;
navigator.mediaDevices?.addEventListener?.("devicechange", () => { if (stream) listCameras(); });

function grabFrame() {
  const w = video.videoWidth;
  const h = video.videoHeight;
  const sideways = rotation % 180 !== 0;
  const canvas = document.createElement("canvas");
  canvas.width = sideways ? h : w;
  canvas.height = sideways ? w : h;
  const ctx = canvas.getContext("2d");
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  ctx.drawImage(video, -w / 2, -h / 2);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}

$("#capture").addEventListener("click", capture);
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || $("#book-view").hidden || e.repeat) return;
  const t = e.target;
  // Space always captures, even with a button focused, unless you're typing.
  if (t.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return;
  e.preventDefault();
  capture();
});

async function capture() {
  if (!stream || !video.videoWidth) return;
  const flash = $("#flash");
  flash.hidden = false;
  flash.style.animation = "none";
  void flash.offsetWidth;  // restart the animation
  flash.style.animation = "";
  setTimeout(() => (flash.hidden = true), 400);
  enqueue(await grabFrame(), "camera.jpg");
}

$("#upload").addEventListener("change", (e) => {
  for (const file of e.target.files) enqueue(file, file.name);
  e.target.value = "";
});

// ---- where the next scan goes ---------------------------------------------

let mode = { kind: "append", page: null };

function setMode(kind, page = null) {
  mode = { kind, page };
  const banner = $("#mode-banner");
  banner.hidden = kind === "append";
  if (kind !== "append") {
    const label = $(".label", page).value || "?";
    $("#mode-text").textContent = kind === "replace"
      ? `Next capture replaces page ${label}.`
      : `Next capture is added after page ${label}.`;
    $(".scanner").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
$("#mode-cancel").addEventListener("click", () => setMode("append"));

function suggestNextLabel() {
  const labels = $$("#pages .label").map((i) => i.value);
  const last = labels[labels.length - 1];
  $("#next-label").placeholder = /^\d+$/.test(last || "") ? String(Number(last) + 1) : "auto";
  $("#next-label").value = "";
}

// ---- upload queue (one at a time, so pages stay in order) ----------------

const scanQueue = [];
let scanning = false;

function enqueue(blob, name) {
  const target = mode;
  setMode("append");
  const typed = $("#next-label").value.trim();
  const guess = typed || $("#next-label").placeholder.replace("auto", "");
  let el;
  if (target.kind === "replace") {
    el = target.page;
    dropIssues(el);
  } else {
    el = pageElement({ html: "", label: guess });
    if (target.kind === "after") target.page.after(el);
    else $("#pages").append(el);
    updateEmpty();
  }
  el.classList.add("busy");
  $(".page-text", el).contentEditable = "false";
  $(".page-info", el).textContent = "Waiting…";
  scanQueue.push({ blob, name, target, el, label: typed });
  // Guess the number of the page after this one.
  $("#next-label").value = "";
  if (target.kind === "append" && /^\d+$/.test(guess)) {
    $("#next-label").placeholder = String(Number(guess) + 1);
  }
  if (target.kind === "append") el.scrollIntoView({ behavior: "smooth", block: "end" });
  runQueue();
}

async function runQueue() {
  if (scanning) return;
  scanning = true;
  while (scanQueue.length) {
    const job = scanQueue[0];
    status(`Reading page… ${scanQueue.length > 1 ? `(${scanQueue.length - 1} more waiting)` : ""}`);
    $(".page-info", job.el).textContent = "Reading text…";
    const form = new FormData();
    form.append("file", job.blob, job.name);
    form.append("mode", job.target.kind);
    if (job.target.page) form.append("page_id", job.target.page.dataset.id);
    if (job.label) form.append("label", job.label);
    try {
      if (job.target.page && dirty.has(job.target.page)) await saveAll();
      const page = await api(`/api/books/${book.id}/pages`, { method: "POST", body: form });
      const fresh = pageElement(page);
      job.el.replaceWith(fresh);
      const recheck = [];
      for (const c of page.cleaned) {  // running header removed from earlier pages
        const el = $(`#pages .page[data-id="${c.id}"]`);
        if (el && !dirty.has(el)) {
          const hadIssues = issues.some((i) => i.el === el);
          dropIssues(el);
          $(".page-text", el).innerHTML = c.html;
          if (hadIssues) recheck.push(el);
        }
      }
      dirty.delete(job.el);
      status(`Page ${page.label} added: ${page.words} words${page.label_found ? "" : " (page number not found on the page; you can type it)"}.`);
      updateContents();
      suggestNextLabel();
      if (me.grammar && $("#auto-check").checked) checkPages([...recheck, fresh], false);
    } catch (ex) {
      job.el.classList.remove("busy");
      job.el.classList.add("failed");
      $(".page-text", job.el).contentEditable = "true";
      $(".page-info", job.el).textContent = `Couldn't read this page: ${ex.message}`;
      if (job.target.kind !== "replace") setTimeout(() => { job.el.remove(); updateEmpty(); }, 8000);
      status(`Couldn't read the page: ${ex.message}`);
    }
    scanQueue.shift();
  }
  scanning = false;
}

function status(text) {
  $("#scan-status").textContent = text;
}

// ==========================================================================
// Table of contents
// ==========================================================================

let contentsTimer = null;
function scheduleContents() {
  clearTimeout(contentsTimer);
  contentsTimer = setTimeout(updateContents, 600);
}

function updateContents() {
  const items = [];
  for (const page of $$("#pages .page")) {
    for (const h of $$(".page-text h1, .page-text h2, .page-text h3", page)) {
      const title = h.innerText.replace(/\s*\n\s*/g, ": ").trim();
      if (title) items.push({ h, title, level: Number(h.tagName[1]), page: $(".label", page).value });
    }
  }
  $("#toc-empty").hidden = items.length > 0;
  $("#toc").replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "#";
    a.className = `level-${item.level}`;
    const title = Object.assign(document.createElement("span"), { className: "title", textContent: item.title });
    const page = Object.assign(document.createElement("span"), { className: "muted", textContent: item.page });
    a.append(title, page);
    a.addEventListener("click", (e) => {
      e.preventDefault();
      item.h.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    li.append(a);
    return li;
  }));
}

// ==========================================================================
// Proofreading (LanguageTool)
// ==========================================================================

let issues = [];   // {el, range, data, id}
let issueSeq = 0;
let currentIssue = null;

for (const tab of $$(".tabs button")) {
  tab.addEventListener("click", () => {
    for (const t of $$(".tabs button")) t.setAttribute("aria-selected", String(t === tab));
    $("#tab-contents").hidden = tab.dataset.tab !== "contents";
    $("#tab-proofread").hidden = tab.dataset.tab !== "proofread";
  });
}
for (const box of $$(".filters input")) box.addEventListener("change", renderIssues);

$("#check-page").addEventListener("click", () => {
  const page = editingPage() || currentPage || $("#pages .page:last-child");
  if (page) checkPages([page], true);
});
$("#check-book").addEventListener("click", () => checkPages($$("#pages .page"), true));

/** The page's text as the checker sees it, and where each character lives
 * in the editor, so a reported position can be turned back into a Range. */
function textMap(pageEl) {
  const root = $(".page-text", pageEl);
  let text = "";
  const pieces = [];   // {node, start}
  for (const block of root.childNodes) {
    if (text) text += "\n\n";
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let node = block; node; node = walker.nextNode()) {
      if (node.nodeType === Node.TEXT_NODE) {
        pieces.push({ node, start: text.length });
        text += node.data;
      } else if (node.tagName === "BR") {
        text += "\n";
      }
    }
  }
  return { text, pieces };
}

function rangeAt(map, start, end) {
  const range = document.createRange();
  let startSet = false;
  for (const { node, start: s } of map.pieces) {
    const e = s + node.data.length;
    if (!startSet && start >= s && start < e) {
      range.setStart(node, start - s);
      startSet = true;
    }
    if (startSet && end > s && end <= e) {
      range.setEnd(node, end - s);
      return range;
    }
  }
  return null;
}

let checking = false;
const checkWaiting = [];

/** Checks pages one after another. Pages asked for while a check is
 * running wait their turn. */
async function checkPages(pages, focusTab) {
  if (focusTab) $(".tabs [data-tab=proofread]").click();
  for (const el of pages) if (!checkWaiting.includes(el)) checkWaiting.push(el);
  if (checking) return;
  checking = true;
  await saveAll();
  let done = 0;
  try {
    while (checkWaiting.length) {
      const el = checkWaiting.shift();
      if (!el.isConnected || !el.dataset.id) continue;
      done += 1;
      $("#check-status").textContent = checkWaiting.length
        ? `Checking page ${done} of ${done + checkWaiting.length}…` : "Checking…";
      const map = textMap(el);
      // A sentence that runs on from the previous page: send its start too,
      // so "she said" at the top of a page isn't flagged as a lowercase start.
      const prev = el.previousElementSibling;
      const prevText = prev ? textMap(prev).text : "";
      const tail = /[.!?:;"'”’)\]…—]\s*$/.test(prevText) ? "" : prevText.slice(prevText.lastIndexOf("\n\n") + 1).trim();
      const prefix = tail && map.text ? tail + " " : "";
      let found;
      try {
        found = await api(`/api/books/${book.id}/check`, { method: "POST", json: { text: prefix + map.text } });
      } catch (ex) {
        $("#check-status").textContent = ex.message;
        checkWaiting.length = 0;
        return;
      }
      dropIssues(el);
      for (const data of found) {
        if (data.offset < prefix.length) continue;  // belongs to the previous page
        data.offset -= prefix.length;
        const range = rangeAt(map, data.offset, data.offset + data.length);
        if (range) issues.push({ id: ++issueSeq, el, range, data });
      }
      renderIssues();
    }
    const total = issues.length;
    $("#check-status").textContent = total ? `${total} thing${total === 1 ? "" : "s"} to look at.` : "No problems found.";
  } finally {
    checking = false;
  }
}

function dropIssues(el) {
  issues = issues.filter((i) => i.el !== el);
  renderIssues();
}

function renderIssues() {
  // Ranges follow the text as it's edited; one that's been deleted collapses.
  issues = issues.filter((i) => i.el.isConnected && !i.range.collapsed);
  const show = new Set($$(".filters input:checked").map((b) => b.dataset.cat));
  const visible = issues.filter((i) => show.has(i.data.category));
  const order = $$("#pages .page");
  visible.sort((a, b) => order.indexOf(a.el) - order.indexOf(b.el)
    || a.range.compareBoundaryPoints(Range.START_TO_START, b.range));

  const list = [];
  let lastEl = null;
  for (const issue of visible) {
    if (issue.el !== lastEl) {
      lastEl = issue.el;
      list.push(Object.assign(document.createElement("li"), {
        className: "page-group", textContent: `Page ${$(".label", issue.el).value || "?"}`,
      }));
    }
    list.push(issueElement(issue));
  }
  $("#issues").replaceChildren(...list);
  const badge = $("#issue-count");
  badge.textContent = issues.length;
  badge.hidden = !issues.length;

  if (window.CSS?.highlights && window.Highlight) {
    for (const cat of ["spelling", "grammar", "punctuation"]) {
      CSS.highlights.set(`issue-${cat}`, new Highlight(...visible.filter((i) => i.data.category === cat).map((i) => i.range)));
    }
    if (currentIssue && issues.includes(currentIssue)) CSS.highlights.set("issue-current", new Highlight(currentIssue.range));
    else CSS.highlights.delete("issue-current");
  }
}

function issueElement(issue) {
  const { data } = issue;
  const li = document.createElement("li");
  li.className = `issue ${data.category}${issue === currentIssue ? " current" : ""}`;

  const context = document.createElement("div");
  context.className = "context";
  const all = textMap(issue.el).text;
  const flagged = issue.range.toString();
  const pos = rangeOffset(issue);
  context.append(
    "…" + all.slice(Math.max(0, pos - 30), pos).replace(/\s+/g, " "),
    Object.assign(document.createElement("b"), { textContent: flagged }),
    all.slice(pos + flagged.length, pos + flagged.length + 30).replace(/\s+/g, " ") + "…",
  );
  const message = Object.assign(document.createElement("div"), { className: "message", textContent: data.message });

  const fixes = document.createElement("div");
  fixes.className = "fixes";
  for (const rep of data.replacements) {
    const b = Object.assign(document.createElement("button"), { className: "fix", textContent: rep || "(delete)" });
    b.addEventListener("click", (e) => { e.stopPropagation(); applyFix(issue, rep); });
    fixes.append(b);
  }
  const ignore = Object.assign(document.createElement("button"), { className: "ghost", textContent: "Ignore" });
  ignore.addEventListener("click", (e) => {
    e.stopPropagation();
    issues = issues.filter((i) => i !== issue);
    renderIssues();
  });
  fixes.append(ignore);
  if (data.category === "spelling") {
    const add = Object.assign(document.createElement("button"), { className: "ghost", textContent: "Add to dictionary" });
    add.addEventListener("click", async (e) => {
      e.stopPropagation();
      await saveBook({ add_word: flagged });
      issues = issues.filter((i) => !(i.data.category === "spelling" && i.range.toString().toLowerCase() === flagged.toLowerCase()));
      renderIssues();
    });
    fixes.append(add);
  } else {
    const off = Object.assign(document.createElement("button"), { className: "ghost", textContent: "Turn off this rule", title: data.rule_description });
    off.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Stop checking for "${data.rule_description || data.rule}" in this book?`)) return;
      await saveBook({ ignore_rule: data.rule });
      issues = issues.filter((i) => i.data.rule !== data.rule);
      renderIssues();
    });
    fixes.append(off);
  }
  li.append(message, context, fixes);
  li.addEventListener("click", () => selectIssue(issue));
  return li;
}

function rangeOffset(issue) {
  const map = textMap(issue.el);
  const piece = map.pieces.find((p) => p.node === issue.range.startContainer);
  return piece ? piece.start + issue.range.startOffset : 0;
}

function selectIssue(issue) {
  currentIssue = issue;
  const sel = getSelection();
  sel.removeAllRanges();
  sel.addRange(issue.range.cloneRange());
  const rect = issue.range.getBoundingClientRect();
  if (rect.top < 120 || rect.bottom > innerHeight - 40) {
    scrollBy({ top: rect.top - innerHeight / 3, behavior: "smooth" });
  }
  renderIssues();
}

function applyFix(issue, replacement) {
  const range = issue.range;
  range.deleteContents();
  if (replacement) range.insertNode(document.createTextNode(replacement));
  issues = issues.filter((i) => i !== issue);
  markDirty(issue.el);
  renderIssues();
}

// Typing changes the text under the ranges; refresh the list now and then.
let issuesTimer = null;
$("#pages").addEventListener("input", () => {
  clearTimeout(issuesTimer);
  issuesTimer = setTimeout(renderIssues, 400);
});

function renderBookLists() {
  if (!book) return;
  const chip = (text, onRemove) => {
    const li = document.createElement("li");
    const x = Object.assign(document.createElement("button"), { textContent: "✕", title: "Remove" });
    x.addEventListener("click", onRemove);
    li.append(text, x);
    return li;
  };
  $("#dictionary").replaceChildren(...book.dictionary.map((w) => chip(w, () =>
    saveBook({ dictionary: book.dictionary.filter((x) => x !== w) }))));
  $("#ignored-rules").replaceChildren(...book.ignored_rules.map((r) => chip(`rule ${r}`, () =>
    saveBook({ ignored_rules: book.ignored_rules.filter((x) => x !== r) }))));
}

start();

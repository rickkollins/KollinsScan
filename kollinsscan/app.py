"""KollinsScan web app (Starlette).

Run:  uvicorn kollinsscan.app:app
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from functools import wraps

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, document, grammar, ocr
from .auth import COOKIE, Auth
from .config import Config
from .db import DB

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_HTML = 500_000  # characters of text per page; a printed page is ~3,000


def error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", name).strip(" .")[:100] or "book"


def next_label(label: str | None) -> str:
    return str(int(label) + 1) if label and label.isdigit() else ""


def create_app(config: Config | None = None) -> Starlette:
    config = config or Config.from_env()
    os.makedirs(config.data_dir, exist_ok=True)
    scans_dir = os.path.join(config.data_dir, "scans")
    db = DB(os.path.join(config.data_dir, "kollinsscan.db"))
    auth = Auth(db, config.admin_user, config.admin_password, config.session_hours)
    max_bytes = config.max_upload_mb * 1024 * 1024
    ocr_slots = asyncio.Semaphore(config.ocr_workers)
    installed = ocr.languages()

    def signed_in(handler):
        """Requires a login session. Changes also need the X-KollinsScan
        header, which other sites can't send (CSRF protection)."""
        @wraps(handler)
        async def wrapper(request: Request):
            if not auth.valid_session(request.cookies.get(COOKIE)):
                return error("Not signed in", 401)
            if request.method != "GET" and request.headers.get("x-kollinsscan") != "1":
                return error("Missing X-KollinsScan header", 403)
            return await handler(request)
        return wrapper

    async def body_json(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def image_path(book_id: int, page_id: int) -> str:
        return os.path.join(scans_dir, str(int(book_id)), f"{int(page_id)}.jpg")

    def get_book(book_id: int) -> dict | None:
        book = db.one("SELECT * FROM books WHERE id = ?", (book_id,))
        if book:
            book["dictionary"] = json.loads(book["dictionary"])
            book["ignored_rules"] = json.loads(book["ignored_rules"])
            book["headers"] = json.loads(book["headers"])
        return book

    def get_pages(book_id: int) -> list[dict]:
        return db.query("SELECT id, seq, label, html, updated FROM pages "
                        "WHERE book_id = ? ORDER BY seq", (book_id,))

    def touch(book_id: int) -> None:
        db.execute("UPDATE books SET updated = ? WHERE id = ?", (time.time(), book_id))

    # ---- pages & session -------------------------------------------------

    async def index(request: Request):
        return FileResponse(os.path.join(STATIC, "index.html"))

    async def health(request: Request):
        return JSONResponse({"ok": True, "version": __version__})

    async def login(request: Request):
        # Behind Caddy, uvicorn --proxy-headers puts the real address here.
        who = request.client.host if request.client else "?"
        if auth.rate_limited(who):
            return error("Too many failed sign-ins. Try again in 15 minutes.", 429)
        body = await body_json(request)
        if not auth.check_password(who, str(body.get("user", "")), str(body.get("password", ""))):
            return error("Wrong user name or password", 401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, auth.create_session(), max_age=auth.session_seconds,
                        httponly=True, secure=config.secure_cookies, samesite="strict")
        return resp

    async def logout(request: Request):
        auth.end_session(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @signed_in
    async def me(request: Request):
        return JSONResponse({
            "user": config.admin_user,
            "version": __version__,
            "languages": installed,
            "default_language": config.default_language,
            "max_upload_mb": config.max_upload_mb,
            "grammar": bool(config.languagetool_url),
        })

    # ---- books -----------------------------------------------------------

    @signed_in
    async def books_list(request: Request):
        return JSONResponse(db.query(
            "SELECT b.id, b.title, b.author, b.language, b.created, b.updated, "
            "(SELECT COUNT(*) FROM pages p WHERE p.book_id = b.id) AS pages "
            "FROM books b ORDER BY b.updated DESC"))

    @signed_in
    async def books_create(request: Request):
        body = await body_json(request)
        title = str(body.get("title", "")).strip()[:200]
        if not title:
            return error("Give the book a title.")
        lang = str(body.get("language") or config.default_language)
        if not ocr.valid_language(lang, installed):
            return error(f"Language {lang!r} isn't installed on the server.")
        now = time.time()
        book_id = db.execute(
            "INSERT INTO books (title, author, language, created, updated) VALUES (?, ?, ?, ?, ?)",
            (title, str(body.get("author", "")).strip()[:200], lang, now, now))
        return JSONResponse({"id": book_id})

    @signed_in
    async def book_detail(request: Request):
        book = get_book(request.path_params["id"])
        if not book:
            return error("No such book", 404)
        book["pages"] = get_pages(book["id"])
        return JSONResponse(book)

    @signed_in
    async def book_update(request: Request):
        book = get_book(request.path_params["id"])
        if not book:
            return error("No such book", 404)
        body = await body_json(request)
        if "title" in body:
            book["title"] = str(body["title"]).strip()[:200] or book["title"]
        if "author" in body:
            book["author"] = str(body["author"]).strip()[:200]
        if "language" in body:
            if not ocr.valid_language(str(body["language"]), installed):
                return error("That language isn't installed on the server.")
            book["language"] = str(body["language"])
        if body.get("add_word"):
            word = str(body["add_word"]).strip()[:100]
            if word and word not in book["dictionary"]:
                book["dictionary"].append(word)
        if body.get("ignore_rule"):
            rule = str(body["ignore_rule"]).strip()[:100]
            if re.fullmatch(r"[A-Z0-9_]+", rule) and rule not in book["ignored_rules"]:
                book["ignored_rules"].append(rule)
        if "ignored_rules" in body and isinstance(body["ignored_rules"], list):
            book["ignored_rules"] = [r for r in book["ignored_rules"] if r in body["ignored_rules"]]
        if "dictionary" in body and isinstance(body["dictionary"], list):
            book["dictionary"] = [w for w in book["dictionary"] if w in body["dictionary"]]
        db.execute("UPDATE books SET title = ?, author = ?, language = ?, dictionary = ?, "
                   "ignored_rules = ?, updated = ? WHERE id = ?",
                   (book["title"], book["author"], book["language"],
                    json.dumps(book["dictionary"]), json.dumps(book["ignored_rules"]),
                    time.time(), book["id"]))
        return JSONResponse(get_book(book["id"]))

    @signed_in
    async def book_delete(request: Request):
        book_id = request.path_params["id"]
        db.execute("DELETE FROM books WHERE id = ?", (book_id,))
        shutil.rmtree(os.path.join(scans_dir, str(int(book_id))), ignore_errors=True)
        return JSONResponse({"ok": True})

    @signed_in
    async def book_contents(request: Request):
        return JSONResponse(document.contents(get_pages(request.path_params["id"])))

    @signed_in
    async def book_export(request: Request):
        book = get_book(request.path_params["id"])
        if not book:
            return error("No such book", 404)
        pages = get_pages(book["id"])
        kind = request.query_params.get("format", "rtf")
        name = safe_filename(book["title"])
        if kind == "txt":
            body, media, ext = document.to_text(book, pages), "text/plain; charset=utf-8", "txt"
        else:
            body, media, ext = document.to_rtf(book, pages), "application/rtf", "rtf"
        return Response(body, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'})

    @signed_in
    async def book_check(request: Request):
        """Grammar/spelling/punctuation check of one page's text."""
        if not config.languagetool_url:
            return error("Grammar checking isn't set up on this server.", 503)
        book = get_book(request.path_params["id"])
        if not book:
            return error("No such book", 404)
        text = str((await body_json(request)).get("text", ""))[:50_000]
        if not text.strip():
            return JSONResponse([])
        try:
            matches = await run_in_threadpool(
                grammar.check, config.languagetool_url, text,
                grammar.lt_language(book["language"]), book["ignored_rules"], book["dictionary"])
        except grammar.GrammarError as e:
            return error(str(e), 502)
        return JSONResponse(matches)

    # ---- pages -----------------------------------------------------------

    @signed_in
    async def page_scan(request: Request):
        """OCR a photo of a page. mode: 'append' (default) adds it at the end,
        'after' inserts it after page_id, 'replace' re-reads page_id."""
        book = get_book(request.path_params["id"])
        if not book:
            return error("No such book", 404)
        if int(request.headers.get("content-length") or 0) > max_bytes + 64 * 1024:
            return error(f"Photos must be under {config.max_upload_mb} MB.", 413)
        try:
            form = await request.form(max_files=1, max_fields=10, max_part_size=max_bytes)
        except Exception:  # malformed multipart, or a part over the limit
            return error(f"Upload one photo under {config.max_upload_mb} MB.")
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return error("No photo was sent.")
            data = await upload.read(max_bytes + 1)
            mode = str(form.get("mode") or "append")
            target_id = str(form.get("page_id") or "")
            typed_label = str(form.get("label") or "").strip()[:20]
        finally:
            await form.close()
        if len(data) > max_bytes:
            return error(f"Photos must be under {config.max_upload_mb} MB.", 413)

        pages = get_pages(book["id"])
        target = next((p for p in pages if str(p["id"]) == target_id), None)
        if mode in ("replace", "after") and not target:
            return error("That page no longer exists.", 404)

        started = time.monotonic()
        try:
            async with ocr_slots:
                result = await run_in_threadpool(
                    ocr.read_page, data, book["language"], config.ocr_timeout,
                    frozenset(book["headers"]))
        except ocr.OCRError as e:
            return error(str(e), 422)

        now = time.time()
        if mode == "replace":
            label = result.label or target["label"]
            db.execute("UPDATE pages SET html = ?, label = ?, updated = ? WHERE id = ?",
                       (result.html, label, now, target["id"]))
            page_id = target["id"]
        else:
            if mode == "after":
                later = [p["seq"] for p in pages if p["seq"] > target["seq"]]
                seq = (target["seq"] + later[0]) / 2 if later else target["seq"] + 1
                guess = next_label(target["label"])
            else:
                seq = (pages[-1]["seq"] + 1) if pages else 1.0
                guess = next_label(pages[-1]["label"]) if pages else ""
            label = result.label or typed_label or guess or str(len(pages) + 1)
            page_id = db.execute(
                "INSERT INTO pages (book_id, seq, label, html, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?)", (book["id"], seq, label, result.html, now, now))
        os.makedirs(os.path.dirname(image_path(book["id"], page_id)), exist_ok=True)
        with open(image_path(book["id"], page_id), "wb") as f:
            f.write(result.image)
        cleaned = []
        if result.header and result.header not in book["headers"]:
            db.execute("UPDATE books SET headers = ? WHERE id = ?",
                       (json.dumps((book["headers"] + [result.header])[-20:]), book["id"]))
            cleaned = drop_header(book["id"], result.header, skip=page_id)
        touch(book["id"])
        page = db.one("SELECT id, seq, label, html, updated FROM pages WHERE id = ?", (page_id,))
        page.update(words=result.words, seconds=round(time.monotonic() - started, 2),
                    label_found=bool(result.label), cleaned=cleaned)
        return JSONResponse(page)

    def drop_header(book_id: int, header: str, skip: int) -> list[dict]:
        """A running header was just recognised; take it off the pages
        scanned before it was known. Returns the pages that changed."""
        changed = []
        for page in get_pages(book_id):
            if page["id"] == skip:
                continue
            blocks = document.from_html(page["html"])
            keep = [b for n, b in enumerate(blocks)
                    if not (n in (0, len(blocks) - 1)
                            and len(document.block_text(b).split()) <= 10
                            and ocr.header_key(document.block_text(b)) == header)]
            if len(keep) != len(blocks):
                html_ = document.to_html(keep)
                db.execute("UPDATE pages SET html = ?, updated = ? WHERE id = ?",
                           (html_, time.time(), page["id"]))
                changed.append({"id": page["id"], "html": html_})
        return changed

    def find_page(request: Request) -> dict | None:
        return db.one("SELECT * FROM pages WHERE id = ?", (request.path_params["id"],))

    @signed_in
    async def page_update(request: Request):
        page = find_page(request)
        if not page:
            return error("No such page", 404)
        body = await body_json(request)
        if "html" in body:
            markup = str(body["html"])
            if len(markup) > MAX_HTML:
                return error("That page is too long.")
            page["html"] = document.clean_html(markup)
        if "label" in body:
            page["label"] = str(body["label"]).strip()[:20]
        db.execute("UPDATE pages SET html = ?, label = ?, updated = ? WHERE id = ?",
                   (page["html"], page["label"], time.time(), page["id"]))
        touch(page["book_id"])
        return JSONResponse({"id": page["id"], "label": page["label"], "html": page["html"]})

    @signed_in
    async def page_move(request: Request):
        page = find_page(request)
        if not page:
            return error("No such page", 404)
        up = (await body_json(request)).get("direction") == "up"
        other = db.one(
            "SELECT id, seq FROM pages WHERE book_id = ? AND seq " + ("<" if up else ">")
            + " ? ORDER BY seq " + ("DESC" if up else "ASC") + " LIMIT 1",
            (page["book_id"], page["seq"]))
        if other:
            db.execute("UPDATE pages SET seq = ? WHERE id = ?", (other["seq"], page["id"]))
            db.execute("UPDATE pages SET seq = ? WHERE id = ?", (page["seq"], other["id"]))
            touch(page["book_id"])
        return JSONResponse({"ok": True})

    @signed_in
    async def page_delete(request: Request):
        page = find_page(request)
        if page:
            db.execute("DELETE FROM pages WHERE id = ?", (page["id"],))
            try:
                os.remove(image_path(page["book_id"], page["id"]))
            except OSError:
                pass
            touch(page["book_id"])
        return JSONResponse({"ok": True})

    @signed_in
    async def page_image(request: Request):
        page = find_page(request)
        path = page and image_path(page["book_id"], page["id"])
        if not path or not os.path.exists(path):
            return error("No photo for this page", 404)
        return FileResponse(path, media_type="image/jpeg")

    app = Starlette(routes=[
        Route("/", index),
        Route("/healthz", health),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/books", books_list),
        Route("/api/books", books_create, methods=["POST"]),
        Route("/api/books/{id:int}", book_detail),
        Route("/api/books/{id:int}", book_update, methods=["PATCH"]),
        Route("/api/books/{id:int}", book_delete, methods=["DELETE"]),
        Route("/api/books/{id:int}/contents", book_contents),
        Route("/api/books/{id:int}/export", book_export),
        Route("/api/books/{id:int}/check", book_check, methods=["POST"]),
        Route("/api/books/{id:int}/pages", page_scan, methods=["POST"]),
        Route("/api/pages/{id:int}", page_update, methods=["PATCH"]),
        Route("/api/pages/{id:int}", page_delete, methods=["DELETE"]),
        Route("/api/pages/{id:int}/move", page_move, methods=["POST"]),
        Route("/api/pages/{id:int}/image", page_image),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])
    app.add_middleware(SecurityHeaders)
    app.state.db = db
    return app


class SecurityHeaders:
    """Adds browser security headers to every response."""

    HEADERS = [
        (b"content-security-policy",
         b"default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
         b"frame-ancestors 'none'"),
        (b"permissions-policy", b"camera=(self), microphone=()"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"same-origin"),
        (b"cache-control", b"no-store"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + self.HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)


def __getattr__(name: str):
    # `uvicorn kollinsscan.app:app` builds the app on first access, so
    # importing this module (e.g. in tests) doesn't read the environment.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)

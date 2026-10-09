"""KollinsScan web app (Starlette).

Run:  uvicorn kollinsscan.app:app
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from functools import wraps

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, ocr
from .auth import COOKIE, Auth
from .config import Config
from .db import DB

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def safe_filename(name: str) -> str:
    base = os.path.basename(name or "").strip() or "image"
    return re.sub(r"[^\w.\- ]", "_", base)[:120]


def create_app(config: Config | None = None) -> Starlette:
    config = config or Config.from_env()
    os.makedirs(config.data_dir, exist_ok=True)
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

    async def index(request: Request):
        return FileResponse(os.path.join(STATIC, "index.html"))

    async def health(request: Request):
        return JSONResponse({"ok": True, "version": __version__})

    async def login(request: Request):
        # Behind Caddy, uvicorn --proxy-headers puts the real address here.
        who = request.client.host if request.client else "?"
        if auth.rate_limited(who):
            return error("Too many failed sign-ins. Try again in 15 minutes.", 429)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict) or not auth.check_password(
                who, str(body.get("user", "")), str(body.get("password", ""))):
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
        })

    @signed_in
    async def run_ocr(request: Request):
        if int(request.headers.get("content-length") or 0) > max_bytes + 64 * 1024:
            return error(f"Images must be under {config.max_upload_mb} MB.", 413)
        try:
            form = await request.form(max_files=1, max_fields=5, max_part_size=max_bytes)
        except Exception:  # malformed multipart, or a part over the limit
            return error(f"Upload one image under {config.max_upload_mb} MB.")
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return error("Choose an image to read.")
            lang = str(form.get("language") or config.default_language)
            if not ocr.valid_language(lang, installed):
                return error(f"Language {lang!r} isn't installed on the server.")
            data = await upload.read(max_bytes + 1)
            filename = safe_filename(upload.filename)
        finally:
            await form.close()
        if len(data) > max_bytes:
            return error(f"Images must be under {config.max_upload_mb} MB.", 413)

        started = time.monotonic()
        try:
            async with ocr_slots:
                text, pages = await run_in_threadpool(
                    ocr.image_to_text, data, lang, config.ocr_timeout)
        except ocr.OCRError as e:
            return error(str(e), 422)
        seconds = round(time.monotonic() - started, 2)
        result_id = db.execute(
            "INSERT INTO results (created, filename, language, pages, seconds, text) "
            "VALUES (?, ?, ?, ?, ?, ?)", (time.time(), filename, lang, pages, seconds, text))
        return JSONResponse({"id": result_id, "filename": filename, "language": lang,
                             "pages": pages, "seconds": seconds, "text": text})

    @signed_in
    async def results_list(request: Request):
        return JSONResponse(db.query(
            "SELECT id, created, filename, language, pages, seconds, "
            "substr(text, 1, 160) AS preview FROM results ORDER BY id DESC LIMIT 500"))

    def find(request: Request) -> dict | None:
        return db.one("SELECT * FROM results WHERE id = ?", (request.path_params["id"],))

    @signed_in
    async def result_detail(request: Request):
        result = find(request)
        return JSONResponse(result) if result else error("Not found", 404)

    @signed_in
    async def result_txt(request: Request):
        result = find(request)
        if not result:
            return error("Not found", 404)
        name = os.path.splitext(result["filename"])[0] + ".txt"
        return Response(result["text"], media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @signed_in
    async def result_delete(request: Request):
        db.execute("DELETE FROM results WHERE id = ?", (request.path_params["id"],))
        return JSONResponse({"ok": True})

    app = Starlette(routes=[
        Route("/", index),
        Route("/healthz", health),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/ocr", run_ocr, methods=["POST"]),
        Route("/api/results", results_list),
        Route("/api/results/{id:int}", result_detail),
        Route("/api/results/{id:int}", result_delete, methods=["DELETE"]),
        Route("/api/results/{id:int}/txt", result_txt),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])
    app.add_middleware(SecurityHeaders)
    return app


class SecurityHeaders:
    """Adds browser security headers to every response."""

    HEADERS = [
        (b"content-security-policy",
         b"default-src 'self'; img-src 'self' blob: data:; frame-ancestors 'none'"),
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

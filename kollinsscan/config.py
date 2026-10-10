"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    data_dir: str = "data"
    admin_user: str = "admin"
    admin_password: str = ""
    secure_cookies: bool = True
    session_hours: int = 24 * 7
    max_upload_mb: int = 20
    # How many pages are OCR'd at the same time (each uses one CPU core).
    ocr_workers: int = 1
    ocr_timeout: int = 120
    default_language: str = "eng"
    # LanguageTool server for grammar checks; empty turns them off.
    languagetool_url: str = ""
    # Anthropic API key for "Ask Claude" on hard pages; empty turns it off.
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            data_dir=env("KOLLINSSCAN_DATA_DIR", "data"),
            admin_user=env("KOLLINSSCAN_ADMIN_USER", "admin"),
            admin_password=env("KOLLINSSCAN_ADMIN_PASSWORD", ""),
            secure_cookies=env("KOLLINSSCAN_SECURE_COOKIES", "1") != "0",
            max_upload_mb=int(env("KOLLINSSCAN_MAX_UPLOAD_MB", "20")),
            ocr_workers=int(env("KOLLINSSCAN_OCR_WORKERS", "1")),
            ocr_timeout=int(env("KOLLINSSCAN_OCR_TIMEOUT", "120")),
            default_language=env("KOLLINSSCAN_DEFAULT_LANGUAGE", "eng"),
            languagetool_url=env("KOLLINSSCAN_LANGUAGETOOL_URL", ""),
            anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
            claude_model=env("KOLLINSSCAN_CLAUDE_MODEL", "") or "claude-opus-5-5",
        )

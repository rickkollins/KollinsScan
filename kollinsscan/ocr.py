"""Image -> text with Tesseract."""

from __future__ import annotations

import io
import re
import subprocess

import pytesseract
from PIL import Image, ImageOps

# Refuse decompression bombs: ~ a 12,000 x 12,000 pixel image.
Image.MAX_IMAGE_PIXELS = 150_000_000

ACCEPTED_TYPES = {"JPEG", "PNG", "TIFF", "BMP", "GIF", "WEBP"}
MAX_PAGES = 50


class OCRError(Exception):
    pass


def languages() -> list[str]:
    """Languages installed for Tesseract, e.g. ['eng', 'spa']."""
    try:
        langs = pytesseract.get_languages(config="")
    except (pytesseract.TesseractError, OSError, subprocess.SubprocessError):
        return []
    return sorted(l for l in langs if l != "osd")


def valid_language(lang: str, installed: list[str]) -> bool:
    # Tesseract accepts combinations such as "eng+spa".
    parts = lang.split("+")
    return bool(re.fullmatch(r"[A-Za-z_]+(\+[A-Za-z_]+)*", lang)) and all(
        p in installed for p in parts)


def _prepare(page: Image.Image) -> Image.Image:
    page = ImageOps.exif_transpose(page)  # phone photos come rotated
    page = page.convert("L")
    # Tesseract works best at ~300 dpi; small images (screenshots of text,
    # thumbnails) get scaled up so letters are tall enough to read.
    if min(page.size) < 1000:
        scale = min(3.0, 1000 / max(1, min(page.size)))
        page = page.resize((int(page.width * scale), int(page.height * scale)),
                           Image.Resampling.LANCZOS)
    return page


def image_to_text(data: bytes, lang: str = "eng", timeout: int = 120) -> tuple[str, int]:
    """Returns (text, page count). Multi-page TIFFs and GIFs are read page
    by page, with a form feed between pages."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()  # catches truncated/corrupt files early
        img = Image.open(io.BytesIO(data))
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as e:
        raise OCRError("That file isn't an image KollinsScan can read.") from e
    if img.format not in ACCEPTED_TYPES:
        raise OCRError(f"{img.format} images aren't supported. "
                       "Use JPG, PNG, TIFF, BMP, GIF or WebP.")

    pages = []
    try:
        for i in range(min(getattr(img, "n_frames", 1), MAX_PAGES)):
            img.seek(i)
            text = pytesseract.image_to_string(_prepare(img.copy()), lang=lang,
                                               timeout=timeout)
            pages.append(text.strip())
    except RuntimeError as e:  # pytesseract raises RuntimeError on timeout
        raise OCRError("The image took too long to read.") from e
    except pytesseract.TesseractError as e:
        raise OCRError(f"Tesseract failed: {e.message}") from e
    except (Image.DecompressionBombError, OSError) as e:
        raise OCRError("That image is too large or damaged.") from e
    return "\n\f\n".join(pages), len(pages)

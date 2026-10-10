"""Re-read a hard page with Claude: it sees the page photo and the current
text, and returns a corrected transcription (formatting included)."""

from __future__ import annotations

import base64
import json

import anthropic

from . import document

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """You transcribe photographed book pages for a book-scanning app. \
You get a photo of one printed page and the text an OCR engine read from it; \
the OCR text may contain recognition errors.

Transcribe the page exactly as printed:
- Fix OCR mistakes (wrong letters, merged or split words, stray symbols) by \
reading the photo. Do not correct, modernise or improve the author's own \
spelling, grammar, punctuation or wording.
- Keep the printed formatting: italic words in <i>, bold in <b>, underlined in \
<u>. Chapter titles in <h1>, section headings in <h2>, smaller headings in \
<h3>; a heading printed on two lines (such as "CHAPTER ONE" over its title) is \
one heading with <br> between the lines. Body paragraphs in <p>.
- Join the lines of each paragraph, and rejoin words hyphenated only because \
they broke at the end of a line. Keep hyphens that belong to the word.
- Leave out the running header, the running footer and the printed page \
number; report the page number separately.
- If text at the very top of the page continues a paragraph from the previous \
page, still start it with <p>.
- Use only the tags p, h1, h2, h3, i, b, u and br, with no attributes.
- If a word cannot be read even in the photo, write your best guess."""

SCHEMA = {
    "type": "object",
    "properties": {
        "html": {"type": "string", "description": "The page text as HTML."},
        "page_number": {"type": "string",
                        "description": "The printed page number, or an empty string."},
        "notes": {"type": "string",
                  "description": "One short sentence on anything uncertain, or empty."},
    },
    "required": ["html", "page_number", "notes"],
    "additionalProperties": False,
}


class ClaudeError(Exception):
    pass


def read_page(api_key: str, model: str, image_jpeg: bytes, ocr_html: str,
              language: str) -> dict:
    """Returns {"html", "page_number", "notes"} for one page. Blocking."""
    client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=180.0)
    ocr_text = "\n\n".join(document.block_text(b) for b in document.from_html(ocr_html))
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            # If the request is declined by a safety classifier, it is re-run
            # on Anthropic's recommended fallback model instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(image_jpeg).decode()}},
                {"type": "text", "text": (
                    f"Language (Tesseract code): {language}\n\n"
                    f"OCR text of this page:\n<ocr>\n{ocr_text}\n</ocr>")},
            ]}],
        )
    except anthropic.AuthenticationError as e:
        raise ClaudeError("The Anthropic API key was rejected. Check "
                          "ANTHROPIC_API_KEY in the server's .env file.") from e
    except anthropic.PermissionDeniedError as e:
        raise ClaudeError("The Anthropic API key isn't allowed to use this model.") from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("Claude is busy (rate limited). Try again in a minute.") from e
    except anthropic.BadRequestError as e:
        raise ClaudeError(f"Claude couldn't read this page: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Claude returned an error ({e.status_code}). "
                          "Try again later.") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError("Couldn't reach Claude. Check the server's internet "
                          "connection.") from e

    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined to read this page.")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("The page was too long for one reading.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ClaudeError("Claude's answer couldn't be understood. Try again.") from e
    return {
        "html": document.clean_html(str(data.get("html", ""))),
        "page_number": str(data.get("page_number", "")).strip()[:20],
        "notes": str(data.get("notes", "")).strip()[:300],
        "model": response.model,
    }

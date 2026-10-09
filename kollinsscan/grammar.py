"""Grammar, spelling and punctuation checks with a LanguageTool server."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

# Tesseract language -> LanguageTool language.
LANGUAGES = {
    "eng": "en-US", "spa": "es", "fra": "fr", "deu": "de-DE", "ita": "it",
    "por": "pt-PT", "nld": "nl", "pol": "pl", "rus": "ru", "ukr": "uk-UA",
    "cat": "ca-ES", "dan": "da-DK", "swe": "sv", "gle": "ga-IE", "ell": "el-GR",
}
SPELLING = {"TYPOS", "MORFOLOGIK"}


class GrammarError(Exception):
    pass


def lt_language(tesseract_lang: str) -> str:
    return LANGUAGES.get(tesseract_lang.split("+")[0], "auto")


def check(server: str, text: str, language: str, disabled_rules: list[str] = (),
          dictionary: list[str] = (), timeout: float = 60) -> list[dict]:
    params = {"text": text, "language": language}
    if disabled_rules:
        params["disabledRules"] = ",".join(disabled_rules)
    req = urllib.request.Request(server.rstrip("/") + "/v2/check",
                                 data=urllib.parse.urlencode(params).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GrammarError("The grammar checker isn't responding. It can take a minute "
                           "to start after the server restarts.") from e

    known = {w.lower() for w in dictionary}
    out = []
    for m in data.get("matches", []):
        word = text[m["offset"]:m["offset"] + m["length"]]
        category = m["rule"]["category"]["id"]
        if category in SPELLING and word.lower() in known:
            continue
        out.append({
            "offset": m["offset"],
            "length": m["length"],
            "text": word,
            "message": m["message"],
            "replacements": [r["value"] for r in m.get("replacements", [])[:5]],
            "rule": m["rule"]["id"],
            "rule_description": m["rule"].get("description", ""),
            "category": "spelling" if category in SPELLING else (
                "punctuation" if category in ("PUNCTUATION", "TYPOGRAPHY") else "grammar"),
        })
    return out

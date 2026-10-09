"""The book's text model, HTML clean-up, and RTF / plain-text export.

A page's text is a list of blocks: {"tag": "p" | "h1" | "h2" | "h3",
"runs": [{"text": ..., "b": bool, "i": bool, "u": bool, "mark": bool}]}.
Run text may contain "\\n" for a line break inside a block. "mark"
highlights words the OCR wasn't sure about; it isn't exported.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

BLOCK_TAGS = {"p": "p", "div": "p", "li": "p", "blockquote": "p", "pre": "p",
              "h1": "h1", "h2": "h2", "h3": "h3", "h4": "h3", "h5": "h3", "h6": "h3"}
INLINE_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "mark": "mark"}
FLAGS = ("b", "i", "u", "mark")
HEADINGS = ("h1", "h2", "h3")
# A paragraph that ends without one of these carries on onto the next page.
SENTENCE_END = re.compile(r"[.!?:;\"'”’)\]…—]\s*$")


def run(text: str, **flags: bool) -> dict:
    return {"text": text, **{f: bool(flags.get(f)) for f in FLAGS}}


def block_text(block: dict) -> str:
    return "".join(r["text"] for r in block["runs"])


# --------------------------------------------------------------------------
# HTML from the editor -> blocks (this is also the sanitizer: anything not
# in the model, such as attributes, scripts and styles, is dropped)
# --------------------------------------------------------------------------

class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.current: dict | None = None
        self.flags = {f: 0 for f in FLAGS}
        self.skip = 0  # inside <script>/<style>

    def _close(self):
        if self.current is not None:
            runs = _tidy_runs(self.current["runs"])
            if runs:
                self.blocks.append({"tag": self.current["tag"], "runs": runs})
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self._close()
            self.current = {"tag": BLOCK_TAGS[tag], "runs": []}
        elif tag in INLINE_TAGS:
            self.flags[INLINE_TAGS[tag]] += 1
        elif tag == "br":
            self._text("\n", raw=True)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self._close()
        elif tag in INLINE_TAGS:
            name = INLINE_TAGS[tag]
            self.flags[name] = max(0, self.flags[name] - 1)

    def handle_data(self, data):
        self._text(data)

    def _text(self, data: str, raw: bool = False):
        if self.skip:
            return
        if not raw:
            data = re.sub(r"[ \t\r\n\f\v\xa0]+", " ", data)
        if not data or (self.current is None and not data.strip()):
            return
        if self.current is None:
            self.current = {"tag": "p", "runs": []}
        self.current["runs"].append(run(data, **{f: n > 0 for f, n in self.flags.items()}))


def _tidy_runs(runs: list[dict]) -> list[dict]:
    """Merges neighbours with the same formatting, collapses spaces and
    trims the ends of the block."""
    merged: list[dict] = []
    for r in runs:
        if merged and all(merged[-1][f] == r[f] for f in FLAGS):
            merged[-1]["text"] += r["text"]
        else:
            merged.append(dict(r))
    for r in merged:
        r["text"] = re.sub(r" *\n *", "\n", re.sub(r" {2,}", " ", r["text"]))
    # Collapse a space that straddles two runs.
    for a, b in zip(merged, merged[1:]):
        if a["text"].endswith(" ") and b["text"].startswith(" "):
            b["text"] = b["text"][1:]
    if merged:
        merged[0]["text"] = merged[0]["text"].lstrip(" \n")
        merged[-1]["text"] = merged[-1]["text"].rstrip(" \n")
    return [r for r in merged if r["text"]]


def from_html(markup: str) -> list[dict]:
    p = _Parser()
    p.feed(markup or "")
    p.close()
    p._close()
    return p.blocks


def to_html(blocks: list[dict]) -> str:
    out = []
    for block in blocks:
        inner = []
        for r in block["runs"]:
            text = html.escape(r["text"], quote=False).replace("\n", "<br>")
            for f in FLAGS:
                if r[f]:
                    text = f"<{f}>{text}</{f}>"
            inner.append(text)
        out.append(f"<{block['tag']}>{''.join(inner)}</{block['tag']}>")
    return "".join(out)


def clean_html(markup: str) -> str:
    return to_html(from_html(markup))


# --------------------------------------------------------------------------
# The whole book
# --------------------------------------------------------------------------

def book_blocks(pages: list[dict]) -> list[tuple[dict, str]]:
    """All blocks in reading order as (block, page label). A paragraph cut
    off at the bottom of a page is joined with its continuation, and a word
    hyphenated across the page break is put back together."""
    out: list[tuple[dict, str]] = []
    for page in pages:
        blocks = from_html(page["html"])
        for n, block in enumerate(blocks):
            prev = out[-1][0] if out else None
            if (n == 0 and prev is not None and prev["tag"] == "p" and block["tag"] == "p"
                    and not SENTENCE_END.search(block_text(prev))):
                _join(prev, block)
            else:
                out.append((block, page["label"]))
    return out


def _join(prev: dict, nxt: dict) -> None:
    last = prev["runs"][-1]
    first = nxt["runs"][0]
    if re.search(r"[A-Za-z]-$", last["text"]) and first["text"][:1].islower():
        last["text"] = last["text"][:-1]
    else:
        last["text"] += " "
    prev["runs"].extend(dict(r) for r in nxt["runs"])
    prev["runs"] = _tidy_runs(prev["runs"])


def heading_title(block: dict) -> str:
    return re.sub(r"\s*\n\s*", ": ", block_text(block)).strip()


def contents(pages: list[dict]) -> list[dict]:
    """Table of contents: every heading, its level and its book page."""
    return [{"title": heading_title(b), "level": int(b["tag"][1]), "page": label}
            for b, label in book_blocks(pages) if b["tag"] in HEADINGS]


def to_text(book: dict, pages: list[dict]) -> str:
    lines = [book["title"]]
    if book.get("author"):
        lines.append(book["author"])
    lines.append("")
    toc = contents(pages)
    if toc:
        lines += ["Contents", ""]
        lines += [f"{'    ' * (e['level'] - 1)}{e['title']} .... {e['page']}" for e in toc]
        lines.append("")
    for block, _ in book_blocks(pages):
        text = block_text(block)
        lines += ["", text.upper() if block["tag"] == "h1" else text, ""] \
            if block["tag"] in HEADINGS else [text, ""]
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------
# RTF
# --------------------------------------------------------------------------

def rtf_escape(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\t":
            out.append("\\tab ")
        elif 32 <= code < 128:
            out.append(ch)
        elif code < 32:
            continue
        else:
            # \uN takes a signed 16-bit number; characters outside the
            # BMP are written as a UTF-16 surrogate pair.
            for unit in _utf16_units(ch):
                out.append(f"\\u{unit - 65536 if unit > 32767 else unit}?")
    return "".join(out)


def _utf16_units(ch: str) -> list[int]:
    data = ch.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def _rtf_runs(runs: list[dict]) -> str:
    parts = []
    for r in runs:
        codes = "".join(c for f, c in (("b", "\\b"), ("i", "\\i"), ("u", "\\ul")) if r[f])
        text = rtf_escape(r["text"])
        parts.append("{" + codes + " " + text + "}" if codes else text)
    return "".join(parts)


# Paragraph formats. US Letter, 1" margins; text width 6.5" = 9360 twips.
_STYLE = {
    "p": "\\s0\\fi360\\sa120\\sl360\\slmult1\\fs24 ",
    "h1": "\\s1\\outlinelevel0\\keepn\\pagebb\\qc\\sb480\\sa360\\b\\fs36 ",
    "h2": "\\s2\\outlinelevel1\\keepn\\qc\\sb360\\sa240\\b\\fs30 ",
    "h3": "\\s3\\outlinelevel2\\keepn\\sb240\\sa120\\b\\fs26 ",
}


def to_rtf(book: dict, pages: list[dict]) -> str:
    title, author = book["title"], book.get("author", "")
    out = [
        "{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1",
        "{\\fonttbl{\\f0\\froman\\fcharset0 Times New Roman;}}",
        "{\\stylesheet{\\s0\\fs24 Normal;}"
        "{\\s1\\outlinelevel0\\keepn\\b\\fs36\\sbasedon0\\snext0 heading 1;}"
        "{\\s2\\outlinelevel1\\keepn\\b\\fs30\\sbasedon0\\snext0 heading 2;}"
        "{\\s3\\outlinelevel2\\keepn\\b\\fs26\\sbasedon0\\snext0 heading 3;}}",
        "{\\info{\\title " + rtf_escape(title) + "}"
        + ("{\\author " + rtf_escape(author) + "}" if author else "") + "}",
        "\\paperw12240\\paperh15840\\margl1440\\margr1440\\margt1440\\margb1440",
        # Title page
        "\\pard\\plain\\qc\\sb2880\\sa240\\b\\fs48 " + rtf_escape(title) + "\\par",
    ]
    if author:
        out.append("\\pard\\plain\\qc\\sa240\\fs32 " + rtf_escape(author) + "\\par")
    out.append("\\page")

    toc = contents(pages)
    if toc:
        # A real TOC field: Word and LibreOffice can update it (right-click >
        # Update Field) to this document's own page numbers. Until then it
        # shows the page numbers of the printed book.
        out.append("\\pard\\plain\\qc\\sa360\\b\\fs32 Contents\\par")
        out.append("{\\field{\\*\\fldinst TOC \\\\o \"1-3\"}{\\fldrslt ")
        for e in toc:
            indent = (e["level"] - 1) * 360
            out.append(f"\\pard\\plain\\li{indent}\\tqr\\tldot\\tx9360\\sa60\\fs24 "
                       + rtf_escape(e["title"]) + "\\tab " + rtf_escape(e["page"]) + "\\par")
        out.append("}}")

    blocks = book_blocks(pages)
    if toc and blocks and blocks[0][0]["tag"] != "h1":
        out.append("\\page")  # chapter headings already start a new page
    for block, _ in blocks:
        out.append("\\pard\\plain" + _STYLE[block["tag"]] + _rtf_runs(block["runs"]) + "\\par")
    out.append("}")
    return "\n".join(out)

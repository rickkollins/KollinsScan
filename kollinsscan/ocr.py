"""A photo of a book page -> clean, editable paragraphs (Tesseract).

Besides reading the words, this:
- evens out the lighting of camera photos before reading,
- rebuilds paragraphs from Tesseract's layout (instead of one line per line)
  and rejoins words hyphenated at the end of a line,
- fixes OCR spacing slips around punctuation,
- finds the printed page number and drops running headers/footers,
- marks chapter and section headings, and
- highlights words Tesseract wasn't sure about, for proofreading.
"""

from __future__ import annotations

import io
import re
import subprocess
from dataclasses import dataclass

import numpy as np
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from .document import run, to_html

# Refuse decompression bombs: ~ a 12,000 x 12,000 pixel image.
Image.MAX_IMAGE_PIXELS = 150_000_000

ACCEPTED_TYPES = {"JPEG", "PNG", "TIFF", "BMP", "GIF", "WEBP"}
UNSURE_BELOW = 50          # Tesseract word confidence (0-100)
EDGE = 0.12                # share of the text height where headers/footers live
SAVED_IMAGE_SIDE = 3000    # longest side of the stored page photo

CHAPTER_RE = re.compile(
    r"^(chapter|part|book|prologue|epilogue|introduction|preface|foreword|afterword|"
    r"acknowledge?ments?|appendix|contents|dedication|interlude|conclusion)\b", re.I)
ROMAN_RE = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$",
                      re.I)
NUMBER_RE = re.compile(r"^\d{1,4}$")


class OCRError(Exception):
    pass


@dataclass
class PageResult:
    html: str
    label: str | None   # the printed page number, if one was found
    header: str | None  # the running header, to recognise it on later pages
    image: bytes        # the photo as a JPEG, upright, for showing beside the text
    words: int


def languages() -> list[str]:
    """Languages installed for Tesseract, e.g. ['eng', 'spa']."""
    try:
        langs = pytesseract.get_languages(config="")
    except (pytesseract.TesseractError, OSError, subprocess.SubprocessError):
        return []
    return sorted(lang for lang in langs if lang != "osd")


def valid_language(lang: str, installed: list[str]) -> bool:
    # Tesseract accepts combinations such as "eng+spa".
    return bool(re.fullmatch(r"[A-Za-z_]+(\+[A-Za-z_]+)*", lang)) and all(
        p in installed for p in lang.split("+"))


# --------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------

def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()  # catches truncated/corrupt files early
        img = Image.open(io.BytesIO(data))
        if img.format not in ACCEPTED_TYPES:
            raise OCRError(f"{img.format} images aren't supported. "
                           "Use JPG, PNG, TIFF, BMP, GIF or WebP.")
        img = ImageOps.exif_transpose(img)  # phone photos come rotated
        img.load()
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as e:
        raise OCRError("That file isn't an image KollinsScan can read.") from e
    return img.convert("RGB")


def stored_jpeg(img: Image.Image) -> bytes:
    img = img.copy()
    img.thumbnail((SAVED_IMAGE_SIDE, SAVED_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


def prepare(img: Image.Image) -> Image.Image:
    """Grey, evenly lit, and big enough for Tesseract."""
    gray = img.convert("L")
    # Tesseract works best at ~300 dpi; enlarge small photos and screenshots.
    if min(gray.size) < 1600:
        scale = min(3.0, 1600 / max(1, min(gray.size)))
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)),
                           Image.Resampling.LANCZOS)
    # Camera photos are darker at the edges and in the book's gutter.
    # Dividing by a heavily blurred copy (the "background") flattens that,
    # so the page reads as white and the ink as black everywhere.
    background = gray.filter(ImageFilter.GaussianBlur(radius=max(15, gray.width // 40)))
    arr = np.asarray(gray, dtype=np.float32) / np.maximum(
        np.asarray(background, dtype=np.float32), 1.0)
    # Paper comes out near 1.0 and ink well below. Anything above 0.9 is
    # paper (pure white, which also wipes out faint shading and JPEG noise);
    # below 0.35 is solid ink; in between keeps the letters' smooth edges.
    levels = (arr - 0.35) / (0.9 - 0.35)
    return Image.fromarray((np.clip(levels, 0, 1) * 255).astype(np.uint8))


# --------------------------------------------------------------------------
# Layout -> paragraphs
# --------------------------------------------------------------------------

@dataclass
class Word:
    text: str
    unsure: bool = False
    italic: bool = False
    bold: bool = False
    slant: float = 0.0     # see word_style()
    stroke: float = 0.0

    def joined(self, other: "Word", text: str) -> "Word":
        """This word merged with the next one (punctuation, a hyphen break)."""
        return Word(text, self.unsure or other.unsure, self.italic, self.bold,
                    self.slant, self.stroke)


@dataclass
class Line:
    words: list[Word]
    top: int
    bottom: int
    left: int
    right: int


@dataclass
class Para:
    lines: list[Line]

    @property
    def words(self) -> list[Word]:
        return [w for line in self.lines for w in line.words]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    top = property(lambda self: self.lines[0].top)
    bottom = property(lambda self: self.lines[-1].bottom)
    left = property(lambda self: min(line.left for line in self.lines))
    right = property(lambda self: max(line.right for line in self.lines))


def text_lines(data: dict, img: Image.Image | None = None) -> list[Line]:
    """Tesseract's word boxes -> text lines, top to bottom. With the page
    image, words printed in italic or bold are marked too."""
    ink = np.asarray(img) < 128 if img is not None else None
    lines: dict[tuple, Line] = {}
    for i, text in enumerate(data["text"]):
        text = text.strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        word = Word(text, conf < UNSURE_BELOW)
        if ink is not None:
            word.slant, word.stroke = word_style(ink[y:y + h, x:x + w])
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line = lines.setdefault(key, Line([], y, y + h, x, x + w))
        line.words.append(word)
        line.top, line.bottom = min(line.top, y), max(line.bottom, y + h)
        line.left, line.right = min(line.left, x), max(line.right, x + w)
    ordered = sorted(lines.values(), key=lambda line: line.top)
    if ink is not None:
        mark_styles([w for line in ordered for w in line.words])
    return ordered


# --------------------------------------------------------------------------
# Italic and bold
# --------------------------------------------------------------------------

ITALIC_SLANT = 0.1      # lean (horizontal shift per pixel of height) beyond normal
BOLD_STROKE = 1.3       # stroke width compared with the page's normal text
MIN_STYLED_WORDS = 15   # too few words on a page to know what "normal" is


def word_style(box: np.ndarray) -> tuple[float, float]:
    """(slant, stroke width) of one word's ink.

    Slant: the shear that makes the letters' strokes most vertical, i.e.
    stacks the ink into the sharpest columns. Upright type comes out
    near 0, italic type at about 0.12-0.3.
    Stroke width: the average length of a horizontal run of ink.
    """
    ys, xs = np.nonzero(box)
    if len(xs) < 20:
        return 0.0, 0.0
    bottom = box.shape[0]

    def sharpness(shear: float) -> int:
        cols = np.round(xs - shear * (bottom - ys)).astype(int)
        return int((np.bincount(cols - cols.min()) ** 2).sum())

    slant = max(np.arange(-0.2, 0.55, 0.025), key=sharpness)
    # Diagonal letters (w, v, x...) look about as sharp leaning either way;
    # a real italic is clearly sharper leaning one way than the other.
    if slant > 0 and sharpness(slant) < sharpness(-slant) * 1.1:
        slant = 0.0
    runs = int((box[:, 1:] & ~box[:, :-1]).sum() + box[:, 0].sum())
    return float(slant), float(box.sum()) / max(runs, 1)


def mark_styles(words: list[Word]) -> None:
    """Marks words that lean or are heavier than the page's normal text."""
    sample = [w for w in words if sum(c.isalpha() for c in w.text) >= 3 and w.stroke]
    if len(sample) < MIN_STYLED_WORDS:
        return
    normal_slant = float(np.median([w.slant for w in sample]))
    normal_stroke = float(np.median([w.stroke for w in sample]))
    for w in words:
        if sum(c.isalpha() for c in w.text) < 2 or not w.stroke:
            continue
        w.italic = w.slant - normal_slant >= ITALIC_SLANT
        w.bold = w.stroke >= normal_stroke * BOLD_STROKE
    # A short word between two italic words ("The *of* Mice") is too small
    # to measure; it goes with its neighbours.
    for a, b, c in zip(words, words[1:], words[2:]):
        if a.italic and c.italic and sum(ch.isalpha() for ch in b.text) < 3:
            b.italic = True


def paragraphs(lines: list[Line]) -> list[Para]:
    """Groups lines into paragraphs the way a reader would: a new paragraph
    starts after a blank gap, at an indented first line, or after a line
    that ends a sentence well short of the right margin."""
    if not lines:
        return []
    heights = sorted(line.bottom - line.top for line in lines)
    lh = heights[len(heights) // 2] or 1                       # typical line height
    lefts = sorted(line.left for line in lines)
    rights = sorted(line.right for line in lines)
    margin_l = lefts[len(lefts) // 10]
    margin_r = rights[len(rights) * 9 // 10]
    gaps = sorted(b.top - a.bottom for a, b in zip(lines, lines[1:])) or [0]
    gap = max(gaps[len(gaps) // 2], 0)                          # typical line gap

    def indented(line: Line) -> bool:
        return line.left - margin_l > lh * 0.8

    paras: list[Para] = []
    for line in lines:
        prev = paras[-1].lines[-1] if paras else None
        new = (
            prev is None
            or line.top - prev.bottom > gap + lh * 0.6
            or (indented(line) and not indented(prev))
            or (prev.right < margin_r - lh * 3
                and re.search(r"[.!?:\"”’]$", prev.words[-1].text))
            or (indented(prev) and not indented(line) and prev.right < margin_r - lh * 3)
        )
        if new:
            paras.append(Para([line]))
        else:
            paras[-1].lines.append(line)
    for p in paras:
        _join_hyphens(p)
        for line in p.lines:
            line.words = fix_punctuation(line.words)
    return paras


def _join_hyphens(p: Para) -> None:
    """'exam-' at the end of a line + 'ple' -> 'example'."""
    for a, b in zip(p.lines, p.lines[1:]):
        if (a.words and b.words and re.search(r"[A-Za-z]-$", a.words[-1].text)
                and len(a.words[-1].text) > 2 and b.words[0].text[:1].islower()):
            end = a.words.pop()
            b.words[0] = end.joined(b.words[0], end.text[:-1] + b.words[0].text)
    p.lines = [line for line in p.lines if line.words]


def fix_punctuation(words: list[Word]) -> list[Word]:
    """Fixes common OCR spacing slips: 'word ,' -> 'word,', '( word' ->
    '(word', 'end.The' -> 'end. The', doubled commas and quote marks."""
    out: list[Word] = []
    attach_next = False
    for word in words:
        text = word.text.replace("''", "\"").replace("``", "\"").replace(",,", ",")
        text = re.sub(r"\.{4,}", "...", text)
        text = re.sub(r"([a-z]{2}[.!?])([A-Z][a-z])", r"\1 \2", text)
        if out and (re.fullmatch(r"[,.;:!?)\]”’]+|'s|n't", text) or attach_next):
            prev = out.pop()
            out.append(prev.joined(word, prev.text + text))
        else:
            out.append(Word(text, word.unsure, word.italic, word.bold, word.slant, word.stroke))
        attach_next = bool(re.fullmatch(r"[(\[“‘]", text))
    for w in out:
        w.text = " ".join(w.text.split())
    return out


def page_number(text: str) -> str | None:
    """The page number in a header/footer line, e.g. '23', 'xii',
    '23 THE GREAT GATSBY' or 'Chapter Four 57'."""
    tokens = text.split()
    for token in (tokens[0], tokens[-1]) if tokens else ():
        token = token.strip(".-–—|[]()")
        if NUMBER_RE.match(token):
            return token
        if len(tokens) == 1 and ROMAN_RE.match(token):
            return token.lower()
    return None


def header_key(text: str) -> str:
    """A running header without its page number, for spotting it again:
    '23 THE GREAT GATSBY' -> 'thegreatgatsby'."""
    return re.sub(r"[^a-z]", "", re.sub(r"\b\d+\b", "", text.lower()))


def margin_number(img: Image.Image, top: int, bottom: int, timeout: int) -> str | None:
    """Tesseract often ignores a page number standing alone in the margin
    (a lone "1" looks like a speck). Look for small, isolated marks just
    above and below the text (top..bottom) and read each one on its own."""
    ink = np.asarray(img) < 128
    h, w = ink.shape
    # Ignore the photo's outer edges and rows that are mostly dark (the
    # page's edge, its shadow, the table): a page number is a small mark.
    ink[:, : w // 50] = False
    ink[:, w - w // 50:] = False
    ink[ink.mean(axis=1) > 0.3] = False
    overlap = int((bottom - top) * EDGE / 3)
    tries = 0
    # Below the text first, then above; nearest the text first. (On a short
    # page, such as a chapter's last, the number is far below the text.)
    for y0, y1, outward in ((max(0, bottom - overlap), h, False),
                            (0, min(h, top + overlap), True)):
        band = ink[y0:y1]
        rows = np.flatnonzero(band.any(axis=1))
        found = _runs_of(rows, max_gap=2)
        for r0, r1 in reversed(found) if outward else found:
            lh = r1 - r0
            if not 8 <= lh <= h * 0.04:
                continue
            cols = np.flatnonzero(band[r0:r1].any(axis=0))
            for c0, c1 in _runs_of(cols, max_gap=lh):  # words, not letters
                if c1 - c0 > lh * 4 or tries >= 8:
                    continue
                tries += 1
                pad = lh
                crop = img.crop((max(0, c0 - pad), max(0, y0 + r0 - pad),
                                 min(w, c1 + pad), min(h, y0 + r1 + pad)))
                crop = ImageOps.expand(crop, border=lh, fill=255)
                # Read as a line of text, then as a single character.
                for psm in (7, 10):
                    try:
                        text = pytesseract.image_to_string(crop, config=f"--psm {psm}",
                                                           timeout=timeout)
                    except (RuntimeError, pytesseract.TesseractError):
                        continue
                    number = page_number(text.strip())
                    if number and len(text.split()) == 1:
                        return number
    return None


def _runs_of(indexes: np.ndarray, max_gap: int) -> list[tuple[int, int]]:
    """[3,4,5,9,10] -> [(3, 6), (9, 11)] for max_gap < 4."""
    out: list[tuple[int, int]] = []
    for i in indexes.tolist():
        if out and i - out[-1][1] <= max_gap:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


@dataclass
class Layout:
    blocks: list[dict]
    label: str | None = None        # printed page number
    header: str | None = None       # running header text, see header_key()


def build_page(paras: list[Para], known_headers: frozenset[str] = frozenset()) -> Layout:
    """Paragraphs -> editor blocks, plus the printed page number."""
    page = Layout([])
    body = list(paras)
    if not body:
        return page
    # Measured from the text, not the photo: the camera usually sees some
    # of the table around the page too.
    top = min(p.top for p in body)
    bottom = max(p.bottom for p in body)
    reach = (bottom - top) * EDGE
    # Running headers and footers: short lines at the very top or bottom
    # that hold a page number or repeat a header seen on earlier pages.
    # Chapter openings ("Chapter 3") are kept.
    for edge in (0, -1, 0, -1):
        if not body:
            break
        p = body[edge]
        in_band = p.bottom < top + reach if edge == 0 else p.top > bottom - reach
        if not in_band or len(p.words) > 10 or CHAPTER_RE.match(p.text):
            continue
        number = page_number(p.text)
        key = header_key(p.text)
        if number or (key and key in known_headers):
            page.label = page.label or number
            if number and key:
                page.header = key
            body.pop(edge)

    if not body:
        return page
    left = min(p.left for p in body)
    right = max(p.right for p in body)
    text_width = max(1, right - left)

    def short_title(p: Para) -> bool:
        return (len(p.lines) <= 2 and len(p.words) <= 10 and not re.search(r"[.,;]$", p.text)
                and any(c.isalpha() for c in p.text))

    def centered(p: Para) -> bool:
        gap_l, gap_r = p.left - left, right - p.right
        return (p.right - p.left) < text_width * 0.7 and abs(gap_l - gap_r) < text_width * 0.12

    for p in body:
        t = p.text
        if (CHAPTER_RE.match(t) and len(p.words) <= 12) or ROMAN_RE.match(t) or NUMBER_RE.match(t):
            tag = "h1"
        elif short_title(p) and ((t.isupper() and sum(c.isalpha() for c in t) >= 3)
                                 or centered(p)):
            tag = "h2"
        else:
            tag = "p"
        # Headings keep their line breaks ("CHAPTER ONE" / "The Long Road").
        runs = _runs(p.lines, keep_lines=tag != "p" or short_title(p), heading=tag != "p")
        prev = page.blocks[-1] if page.blocks else None
        if (tag != "h1" and short_title(p) and prev and prev["tag"] == "h1"
                and "\n" not in "".join(r["text"] for r in prev["runs"])):
            for r in runs:
                r["b"] = False  # headings are bold anyway
            prev["runs"] += [run("\n")] + runs  # chapter number + its title
            continue
        page.blocks.append({"tag": tag, "runs": runs})
    return page


def _runs(lines: list[Line], keep_lines: bool = False, heading: bool = False) -> list[dict]:
    """Words -> formatted runs. Spaces between differently formatted words
    stay outside the formatting. Headings are bold anyway, so bold isn't
    marked inside them."""
    runs: list[dict] = []
    for n, line in enumerate(lines):
        for k, word in enumerate(line.words):
            sep = "" if n == k == 0 else ("\n" if keep_lines and k == 0 else " ")
            style = {"mark": word.unsure, "i": word.italic, "b": word.bold and not heading}
            if runs and all(runs[-1][f] == v for f, v in style.items()):
                runs[-1]["text"] += sep + word.text
            elif runs and any(runs[-1][f] for f in style) and not any(style.values()):
                runs.append(run(sep + word.text, **style))  # space stays plain
            elif runs:
                runs[-1]["text"] += sep
                runs.append(run(word.text, **style))
            else:
                runs.append(run(sep + word.text, **style))
    return runs


def read_page(data: bytes, lang: str = "eng", timeout: int = 120,
              known_headers: frozenset[str] = frozenset()) -> PageResult:
    img = open_image(data)
    prepared = prepare(img)
    try:
        layout = pytesseract.image_to_data(prepared, lang=lang, timeout=timeout,
                                           output_type=pytesseract.Output.DICT)
    except RuntimeError as e:  # pytesseract raises RuntimeError on timeout
        raise OCRError("The page took too long to read.") from e
    except pytesseract.TesseractError as e:
        raise OCRError(f"Tesseract failed: {e.message}") from e
    lines = text_lines(layout, prepared)
    page = build_page(paragraphs(lines), known_headers)
    label = page.label
    if not label and lines:
        label = margin_number(prepared, min(line.top for line in lines),
                              max(line.bottom for line in lines), timeout)
    words = sum(len("".join(r["text"] for r in b["runs"]).split()) for b in page.blocks)
    return PageResult(to_html(page.blocks), label, page.header, stored_jpeg(img), words)

"""Draws photo-like test pages of a book: uneven lighting (a dark gutter),
slight blur, a running header, a chapter opening and a page number."""

import io
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT = 1700, 2300
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-{}.ttf"
HAVE_STYLE_FONTS = all(os.path.exists(SERIF.format(s)) for s in ("Regular", "Italic", "Bold"))


def page(lines, header=None, footer=None) -> bytes:
    img = Image.new("L", (WIDTH, HEIGHT), 245)
    draw = ImageDraw.Draw(img)
    body = ImageFont.load_default(size=34)
    heading = ImageFont.load_default(size=54)
    small = ImageFont.load_default(size=28)
    if header:
        draw.text((150, 90), header, fill=30, font=small)
    y = 260
    for kind, text in lines:
        if kind == "heading":
            x = (WIDTH - draw.textlength(text, font=heading)) / 2
            draw.text((x, y), text, fill=20, font=heading)
            y += 110
        elif kind == "gap":
            y += 40
        else:  # "first" lines of a paragraph are indented
            draw.text((220 if kind == "first" else 150, y), text, fill=25, font=body)
            y += 52
    if footer:
        x = (WIDTH - draw.textlength(footer, font=small)) / 2
        draw.text((x, HEIGHT - 140), footer, fill=30, font=small)

    # Uneven lighting: a dark left edge (the gutter) and a darker bottom.
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    shade = 0.55 + 0.45 * np.clip(xx / (WIDTH * 0.5), 0, 1) * (1 - 0.25 * yy / HEIGHT)
    arr = np.asarray(img, dtype=np.float32) * shade
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()


def page_one() -> bytes:
    return page([
        ("heading", "CHAPTER ONE"), ("heading", "The Long Road"), ("gap", ""),
        ("first", "It was late in the autumn when Martha first saw the old"),
        ("line", "house on the hill. The windows were dark, and the garden"),
        ("line", "had long since gone to seed. She stood at the gate for a"),
        ("line", "long time , wondering whether anyone still lived there.The"),
        ("line", "path was overgrown, and the door looked as though it had"),
        ("line", "not been opened in years. Still, something drew her for-"),
        ("line", "ward, and she began to climb the steps."),
        ("first", "Inside, the hall smelled of dust and old paper. A clock"),
        ("line", "ticked somewhere in the darkness, steady and patient, as"),
        ("line", "if it had been waiting for her all this time and would"),
    ], header="THE HOUSE ON THE HILL", footer="1")


def page_two() -> bytes:
    return page([
        ("line", "go on waiting long after she had gone. She called out,"),
        ("line", "but no one answered."),
        ("first", "The next morning she returned with a lantern."),
    ], header="THE HOUSE ON THE HILL                                    2")


def styled_page(plain: bool = False) -> bytes:
    """A camera-like photo of a paragraph with italic and bold words (all
    regular type with plain=True)."""
    fonts = {k: ImageFont.truetype(SERIF.format(v), 34)
             for k, v in (("r", "Regular"), ("i", "Italic"), ("b", "Bold"))}
    lines = [
        [("r", "It was late when"), ("i", "The Long Goodbye"), ("r", "arrived in the post.")],
        [("r", "She read it twice and"), ("b", "never"), ("r", "opened it again, though")],
        [("r", "the title,"), ("i", "mon cher ami,"), ("r", "stayed with her for years.")],
        [("r", "Nothing about the house was ordinary; the door was"), ("i", "always")],
        [("r", "left open and the windows were painted shut.")],
    ]
    img = Image.new("L", (1700, 600), 245)
    draw = ImageDraw.Draw(img)
    y = 60
    for line in lines:
        x = 150
        for kind, text in line:
            font = fonts["r" if plain else kind]
            draw.text((x, y), text, font=font, fill=25)
            x += draw.textlength(text + " ", font=font)
        y += 52
    # Slightly soft and unevenly lit, like a camera photo.
    yy, xx = np.mgrid[0:img.height, 0:img.width]
    shade = 0.7 + 0.3 * np.clip(xx / (img.width * 0.5), 0, 1)
    arr = np.asarray(img, dtype=np.float32) * shade
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()


if __name__ == "__main__":
    for name, data in (("page1.jpg", page_one()), ("page2.jpg", page_two())):
        with open(name, "wb") as f:
            f.write(data)

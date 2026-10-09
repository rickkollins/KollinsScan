import io
import os
import shutil
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kollinsscan import ocr  # noqa: E402
from kollinsscan.app import create_app  # noqa: E402
from kollinsscan.config import Config  # noqa: E402

HAVE_TESSERACT = shutil.which("tesseract") is not None
HEADERS = {"X-KollinsScan": "1"}


def text_image(text: str, fmt: str = "PNG") -> bytes:
    img = Image.new("RGB", (900, 160), "white")
    draw = ImageDraw.Draw(img)
    draw.text((30, 40), text, fill="black", font=ImageFont.load_default(size=48))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app(Config(data_dir=self.tmp, admin_user="rick",
                                     admin_password="correct horse", secure_cookies=False,
                                     max_upload_mb=1))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self):
        r = self.client.post("/api/login", json={"user": "rick", "password": "correct horse"})
        self.assertEqual(r.status_code, 200, r.text)


class AuthTests(AppTestCase):
    def test_api_needs_login(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/results").status_code, 401)

    def test_wrong_password(self):
        r = self.client.post("/api/login", json={"user": "rick", "password": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_rate_limit(self):
        for _ in range(10):
            self.client.post("/api/login", json={"user": "rick", "password": "nope"})
        r = self.client.post("/api/login", json={"user": "rick", "password": "correct horse"})
        self.assertEqual(r.status_code, 429)

    def test_login_and_logout(self):
        self.login()
        self.assertEqual(self.client.get("/api/me").json()["user"], "rick")
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_no_password_configured_refuses_everyone(self):
        app = create_app(Config(data_dir=self.tmp, admin_password="", secure_cookies=False))
        with TestClient(app) as c:
            r = c.post("/api/login", json={"user": "admin", "password": ""})
            self.assertEqual(r.status_code, 401)

    def test_changes_need_csrf_header(self):
        self.login()
        r = self.client.post("/api/ocr", files={"file": ("a.png", b"x", "image/png")})
        self.assertEqual(r.status_code, 403)

    def test_security_headers(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])


class UploadTests(AppTestCase):
    def test_rejects_non_image(self):
        self.login()
        r = self.client.post("/api/ocr", headers=HEADERS,
                             files={"file": ("notes.txt", b"hello", "text/plain")})
        self.assertEqual(r.status_code, 422)

    def test_rejects_large_upload(self):
        self.login()
        big = b"0" * (2 * 1024 * 1024)
        r = self.client.post("/api/ocr", headers=HEADERS,
                             files={"file": ("big.png", big, "image/png")})
        self.assertIn(r.status_code, (400, 413))

    def test_rejects_unknown_language(self):
        self.login()
        r = self.client.post("/api/ocr", headers=HEADERS, data={"language": "xx;rm"},
                             files={"file": ("a.png", text_image("hi"), "image/png")})
        self.assertEqual(r.status_code, 400)

    def test_language_validation(self):
        self.assertTrue(ocr.valid_language("eng+spa", ["eng", "spa"]))
        self.assertFalse(ocr.valid_language("eng+fra", ["eng", "spa"]))
        self.assertFalse(ocr.valid_language("eng -c x", ["eng"]))


@unittest.skipUnless(HAVE_TESSERACT, "tesseract is not installed")
class OCRTests(AppTestCase):
    def test_reads_text_and_keeps_history(self):
        self.login()
        r = self.client.post("/api/ocr", headers=HEADERS, data={"language": "eng"},
                             files={"file": ("../../hello?.png", text_image("Hello KollinsScan"),
                                             "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()
        self.assertIn("KollinsScan", result["text"])
        self.assertEqual(result["filename"], "hello_.png")

        items = self.client.get("/api/results").json()
        self.assertEqual([i["id"] for i in items], [result["id"]])
        txt = self.client.get(f"/api/results/{result['id']}/txt")
        self.assertIn("KollinsScan", txt.text)
        self.assertIn("hello_.txt", txt.headers["content-disposition"])

        self.client.delete(f"/api/results/{result['id']}", headers=HEADERS)
        self.assertEqual(self.client.get("/api/results").json(), [])

    def test_multi_page_tiff(self):
        pages = [Image.open(io.BytesIO(text_image(t))) for t in ("First page", "Second page")]
        buf = io.BytesIO()
        pages[0].save(buf, "TIFF", save_all=True, append_images=pages[1:])
        text, count = ocr.image_to_text(buf.getvalue())
        self.assertEqual(count, 2)
        self.assertIn("First", text)
        self.assertIn("Second", text)


if __name__ == "__main__":
    unittest.main()

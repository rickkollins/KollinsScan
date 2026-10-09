import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import make_pages  # noqa: E402
from kollinsscan import document, ocr  # noqa: E402
from kollinsscan.app import create_app  # noqa: E402
from kollinsscan.config import Config  # noqa: E402

HAVE_TESSERACT = shutil.which("tesseract") is not None
HEADERS = {"X-KollinsScan": "1"}


class FakeLanguageTool(BaseHTTPRequestHandler):
    """Answers /v2/check like LanguageTool, flagging 'fora' and 'Martha'."""
    requests: list[dict] = []

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        FakeLanguageTool.requests.append(form)
        text = form["text"][0]
        matches = []
        for word, cat, fixes in (("fora", "TYPOS", ["for a"]), ("Martha", "TYPOS", ["Martian"])):
            if word in text:
                matches.append({"offset": text.index(word), "length": len(word),
                                "message": "Possible spelling mistake",
                                "replacements": [{"value": v} for v in fixes],
                                "rule": {"id": "MORFOLOGIK_RULE_EN_US", "description": "Spelling",
                                         "category": {"id": cat}}})
        body = json.dumps({"matches": matches}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AppTestCase(unittest.TestCase):
    languagetool = ""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app(Config(data_dir=self.tmp, admin_user="rick",
                                     admin_password="correct horse", secure_cookies=False,
                                     max_upload_mb=2, languagetool_url=self.languagetool))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self):
        r = self.client.post("/api/login", json={"user": "rick", "password": "correct horse"})
        self.assertEqual(r.status_code, 200, r.text)

    def new_book(self, **fields) -> int:
        r = self.client.post("/api/books", headers=HEADERS,
                             json={"title": "The House on the Hill", "author": "A. Writer", **fields})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def scan(self, book_id, data, **form):
        return self.client.post(f"/api/books/{book_id}/pages", headers=HEADERS, data=form,
                                files={"file": ("page.jpg", data, "image/jpeg")})


class AuthTests(AppTestCase):
    def test_api_needs_login(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/books").status_code, 401)

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
        r = self.client.post("/api/books", json={"title": "x"})
        self.assertEqual(r.status_code, 403)

    def test_security_headers(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertIn("camera=(self)", r.headers["permissions-policy"])


class DocumentTests(unittest.TestCase):
    def test_html_is_cleaned(self):
        dirty = ('<div style="x" onclick="evil()">Hello <b>bold</b><script>alert(1)</script>'
                 '<span class="y"> and <em>slanted</em></span></div><h4>Small</h4>'
                 'stray text<img src=x onerror=alert(1)><a href="javascript:x">link</a>')
        self.assertEqual(
            document.clean_html(dirty),
            "<p>Hello <b>bold</b> and <i>slanted</i></p><h3>Small</h3><p>stray textlink</p>")

    def test_round_trip_keeps_marks_and_breaks(self):
        html = "<h1>CHAPTER ONE<br>The Long Road</h1><p>A <mark>wurd</mark> &amp; more</p>"
        self.assertEqual(document.clean_html(html), html)

    def test_paragraph_continues_across_pages(self):
        pages = [{"label": "1", "html": "<p>Something drew her for-</p>"},
                 {"label": "2", "html": "<p>ward, and she went in.</p><p>Next.</p>"}]
        blocks = document.book_blocks(pages)
        self.assertEqual([document.block_text(b) for b, _ in blocks],
                         ["Something drew her forward, and she went in.", "Next."])
        pages[0]["html"] = "<p>She called out,</p>"
        pages[1]["html"] = "<p>but no one answered.</p>"
        self.assertEqual(document.block_text(document.book_blocks(pages)[0][0]),
                         "She called out, but no one answered.")

    def test_finished_paragraph_is_not_joined(self):
        pages = [{"label": "1", "html": "<p>The end.</p>"}, {"label": "2", "html": "<p>Then</p>"}]
        self.assertEqual(len(document.book_blocks(pages)), 2)

    def test_contents(self):
        pages = [{"label": "1", "html": "<h1>CHAPTER ONE<br>The Long Road</h1><p>Text.</p>"},
                 {"label": "9", "html": "<p>More.</p><h2>A Section</h2><h1>Chapter Two</h1>"}]
        self.assertEqual(document.contents(pages), [
            {"title": "CHAPTER ONE: The Long Road", "level": 1, "page": "1"},
            {"title": "A Section", "level": 2, "page": "9"},
            {"title": "Chapter Two", "level": 1, "page": "9"},
        ])

    def test_rtf_escaping(self):
        self.assertEqual(document.rtf_escape("a{b}\\c"), "a\\{b\\}\\\\c")
        self.assertEqual(document.rtf_escape("café “x”"), "caf\\u233? \\u8220?x\\u8221?")
        self.assertEqual(document.rtf_escape("😀"), "\\u-10179?\\u-8704?")  # surrogate pair

    def test_rtf_document(self):
        book = {"title": "The House", "author": "A. Writer"}
        pages = [{"label": "1", "html": "<h1>CHAPTER ONE</h1><p>It was <i>late</i> and <mark>dark</mark>.</p>"}]
        rtf = document.to_rtf(book, pages)
        self.assertTrue(rtf.startswith("{\\rtf1") and rtf.endswith("}"))
        self.assertEqual(rtf.count("{") - rtf.count("\\{"), rtf.count("}") - rtf.count("\\}"))
        self.assertIn("{\\field{\\*\\fldinst TOC \\\\o \"1-3\"}", rtf)
        self.assertIn("CHAPTER ONE\\tab 1\\par", rtf)
        self.assertIn("\\outlinelevel0", rtf)
        self.assertIn("It was {\\i late} and dark.", rtf)  # highlights aren't exported


class OCRUnitTests(unittest.TestCase):
    def test_punctuation_fixes(self):
        words = [(w, False) for w in "She stood , wondering ( quietly ) there.The end".split()]
        self.assertEqual(" ".join(w for w, _ in ocr.fix_punctuation(words)),
                         "She stood, wondering (quietly) there. The end")

    def test_page_numbers(self):
        self.assertEqual(ocr.page_number("23"), "23")
        self.assertEqual(ocr.page_number("THE GREAT GATSBY 57"), "57")
        self.assertEqual(ocr.page_number("xii"), "xii")
        self.assertIsNone(ocr.page_number("THE GREAT GATSBY"))
        self.assertEqual(ocr.header_key("57 The Great Gatsby"), "thegreatgatsby")

    def test_language_validation(self):
        self.assertTrue(ocr.valid_language("eng+spa", ["eng", "spa"]))
        self.assertFalse(ocr.valid_language("eng+fra", ["eng", "spa"]))
        self.assertFalse(ocr.valid_language("eng -c x", ["eng"]))


@unittest.skipUnless(HAVE_TESSERACT, "tesseract is not installed")
class OCRPageTests(unittest.TestCase):
    def test_known_header_is_dropped(self):
        result = ocr.read_page(make_pages.page_one(), known_headers=frozenset({"thehouseonthehill"}))
        self.assertEqual(document.from_html(result.html)[0]["tag"], "h1")

    def test_chapter_opening(self):
        result = ocr.read_page(make_pages.page_one())
        self.assertEqual(result.label, "1")
        blocks = document.from_html(result.html)
        # The running header has no page number here, so it's only known
        # (and removed) once a later page shows it with one.
        self.assertEqual(document.block_text(blocks.pop(0)), "THE HOUSE ON THE HILL")
        self.assertEqual(blocks[0]["tag"], "h1")
        self.assertEqual(document.block_text(blocks[0]), "CHAPTER ONE\nThe Long Road")
        first = document.block_text(blocks[1])
        self.assertTrue(first.startswith("It was late in the autumn"))
        self.assertIn("long time, wondering", first)         # " ," fixed
        self.assertIn("lived there. The path", first)        # "there.The" fixed
        self.assertIn("drew her forward, and", first)        # hyphen rejoined
        self.assertTrue(document.block_text(blocks[2]).startswith("Inside, the hall"))

    def test_running_header_and_number(self):
        result = ocr.read_page(make_pages.page_two())
        self.assertEqual(result.label, "2")
        self.assertEqual(result.header, "thehouseonthehill")
        texts = [document.block_text(b) for b in document.from_html(result.html)]
        self.assertEqual(texts, ["go on waiting long after she had gone. She called out, "
                                 "but no one answered.",
                                 "The next morning she returned with a lantern."])


class BookTests(AppTestCase):
    def test_book_lifecycle(self):
        self.login()
        book_id = self.new_book()
        self.assertEqual(self.client.get("/api/books").json()[0]["pages"], 0)
        r = self.client.patch(f"/api/books/{book_id}", headers=HEADERS,
                              json={"title": "New Title", "add_word": "Martha"})
        self.assertEqual(r.json()["title"], "New Title")
        self.assertEqual(r.json()["dictionary"], ["Martha"])
        self.client.delete(f"/api/books/{book_id}", headers=HEADERS)
        self.assertEqual(self.client.get(f"/api/books/{book_id}").status_code, 404)

    def test_rejects_unknown_language(self):
        self.login()
        r = self.client.post("/api/books", headers=HEADERS, json={"title": "x", "language": "xx;rm"})
        self.assertEqual(r.status_code, 400)

    def test_rejects_non_image_and_large_upload(self):
        self.login()
        book_id = self.new_book()
        self.assertEqual(self.scan(book_id, b"hello").status_code, 422)
        self.assertIn(self.scan(book_id, b"0" * (3 * 1024 * 1024)).status_code, (400, 413))

    @unittest.skipUnless(HAVE_TESSERACT, "tesseract is not installed")
    def test_scan_edit_and_export(self):
        self.login()
        book_id = self.new_book()
        first = self.scan(book_id, make_pages.page_one()).json()
        self.assertEqual(first["label"], "1")
        # Page 2 shows the running header with its number, so it's learned
        # and also taken off page 1, where it had no number.
        second = self.scan(book_id, make_pages.page_two()).json()
        self.assertEqual(second["label"], "2")
        self.assertEqual(self.client.get(f"/api/books/{book_id}").json()["headers"],
                         ["thehouseonthehill"])

        # A missed page goes in between.
        between = self.scan(book_id, make_pages.page_two(), mode="after",
                            page_id=str(first["id"])).json()
        pages = self.client.get(f"/api/books/{book_id}").json()["pages"]
        self.assertEqual([p["id"] for p in pages], [first["id"], between["id"], second["id"]])
        self.client.delete(f"/api/pages/{between['id']}", headers=HEADERS)
        self.assertEqual(self.client.get(f"/api/pages/{between['id']}/image").status_code, 404)

        # Editing is sanitised.
        r = self.client.patch(f"/api/pages/{second['id']}", headers=HEADERS, json={
            "html": second["html"] + '<p onclick="x()">An <b>edit</b><script>bad()</script></p>'})
        self.assertTrue(r.json()["html"].endswith("<p>An <b>edit</b></p>"))

        self.assertEqual(self.client.get(f"/api/pages/{first['id']}/image").headers["content-type"],
                         "image/jpeg")
        toc = self.client.get(f"/api/books/{book_id}/contents").json()
        self.assertEqual(toc, [{"title": "CHAPTER ONE: The Long Road", "level": 1, "page": "1"}])

        rtf = self.client.get(f"/api/books/{book_id}/export?format=rtf")
        self.assertEqual(rtf.headers["content-type"], "application/rtf")
        self.assertIn('filename="The House on the Hill.rtf"', rtf.headers["content-disposition"])
        self.assertIn("CHAPTER ONE\\line The Long Road", rtf.text)
        self.assertNotIn("THE HOUSE ON THE HILL", rtf.text)
        # The sentence that ran over the page break is one paragraph again.
        self.assertIn("all this time and would go on waiting", rtf.text)

        txt = self.client.get(f"/api/books/{book_id}/export?format=txt").text
        self.assertIn("Contents", txt)
        self.assertIn("would go on waiting", txt)

    def test_move_page(self):
        self.login()
        book_id = self.new_book()
        db = self.app.state.db
        ids = [db.execute("INSERT INTO pages (book_id, seq, label, html, created, updated) "
                          "VALUES (?, ?, ?, '', 0, 0)", (book_id, n + 1, str(n + 1)))
               for n in range(3)]
        self.client.post(f"/api/pages/{ids[2]}/move", headers=HEADERS, json={"direction": "up"})
        order = [p["id"] for p in self.client.get(f"/api/books/{book_id}").json()["pages"]]
        self.assertEqual(order, [ids[0], ids[2], ids[1]])
        self.client.post(f"/api/pages/{ids[0]}/move", headers=HEADERS, json={"direction": "up"})
        order = [p["id"] for p in self.client.get(f"/api/books/{book_id}").json()["pages"]]
        self.assertEqual(order, [ids[0], ids[2], ids[1]])  # already first


class GrammarTests(AppTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeLanguageTool)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.languagetool = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_check(self):
        self.login()
        book_id = self.new_book()
        r = self.client.post(f"/api/books/{book_id}/check", headers=HEADERS,
                             json={"text": "Martha stood at the gate fora long time."})
        found = r.json()
        self.assertEqual([m["text"] for m in found], ["fora", "Martha"])
        self.assertEqual(found[0]["replacements"], ["for a"])
        self.assertEqual(found[0]["category"], "spelling")
        self.assertEqual(FakeLanguageTool.requests[-1]["language"], ["en-US"])

        # Words in the book's dictionary aren't flagged; switched-off rules
        # are passed on to LanguageTool.
        self.client.patch(f"/api/books/{book_id}", headers=HEADERS,
                          json={"add_word": "martha", "ignore_rule": "EN_QUOTES"})
        found = self.client.post(f"/api/books/{book_id}/check", headers=HEADERS,
                                 json={"text": "Martha stood at the gate fora long time."}).json()
        self.assertEqual([m["text"] for m in found], ["fora"])
        self.assertEqual(FakeLanguageTool.requests[-1]["disabledRules"], ["EN_QUOTES"])

    def test_off_when_not_configured(self):
        app = create_app(Config(data_dir=self.tmp, admin_password="pw", secure_cookies=False))
        with TestClient(app) as c:
            c.post("/api/login", json={"user": "admin", "password": "pw"})
            self.assertFalse(c.get("/api/me").json()["grammar"])
            book = c.post("/api/books", headers=HEADERS, json={"title": "x"}).json()["id"]
            r = c.post(f"/api/books/{book}/check", headers=HEADERS, json={"text": "x"})
            self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()

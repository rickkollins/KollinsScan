# KollinsScan

Scan a book one page at a time into a single, editable document, proofread it, and download it
as an **RTF** file (opens in Word, LibreOffice, Pages, Google Docs and WordPad) with a
**table of contents**.

KollinsScan runs on your own server (for example a Hostinger VPS). You use it in a web browser on
the computer your **USB camera** is plugged into: the browser shows the camera, you press
**Space** for each page, and the server reads the text with the free
[Tesseract](https://github.com/tesseract-ocr/tesseract) OCR engine. Neither the photos nor the
text are sent to any outside service.

## What it does with each page

- **Reads the text** after evening out the lighting of the photo (dark gutters and shadows).
- **Rebuilds paragraphs** instead of one line per line, and rejoins words hyphenated at the
  end of a line (`for-` / `ward` → `forward`).
- **Fixes OCR punctuation slips** such as `word ,` → `word,` and `there.The` → `there. The`.
- **Finds the printed page number** and uses it as the page's number. Running headers (the
  book title or chapter name at the top of each page) are recognised and removed.
- **Finds chapter headings** (`Chapter 3`, `CHAPTER THREE`, `Part Two`, `Prologue`, a Roman
  numeral, a centred title...) for the table of contents.
- **Highlights words in yellow** that the scanner wasn't sure about, so you know where to look.
- **Checks spelling, grammar and punctuation** with [LanguageTool](https://languagetool.org/)
  (also running on your server), and lists each problem with one-click fixes.

All pages appear as one continuous document you can edit directly. A sentence that runs from
one page onto the next is joined back into one paragraph in the RTF.

## Using it

1. Sign in, enter the book's title and author, and click **Create and start scanning**.
2. Click **Turn on camera** and allow camera access when the browser asks. If you have more than
   one camera, pick the USB camera from the **Camera** list. If the picture is sideways (the
   camera is mounted on its side), click **Rotate** until the page reads upright. KollinsScan
   remembers these settings.
3. Put the first page under the camera and press **Space** (or click **Capture page**). Turn the
   page and press Space again. You don't have to wait: pages are read in order in the
   background.
4. Fix anything you like right in the text. Changes save automatically.
   - **Chapter** / **Section** turn the line with the cursor into a heading for the table of
     contents; **¶ Text** turns it back into normal text. **B**, **I** and **U** format the
     selection.
   - **Photo** shows the page's photo beside its text, for checking yellow words.
   - **Re-scan** captures that page again (to replace a blurry one); **Add after** scans a page
     you missed into the right place; ↑ ↓ reorder; ✕ deletes.
   - Click a page's number to correct it.
5. **Proofread** (right-hand panel): each new page is checked automatically, or click
   **Check whole book**. Click a problem to jump to it, then click a suggested fix, **Ignore**,
   **Add to dictionary** (for names and invented words in the book) or **Turn off this rule**.
6. **Download RTF**. It starts with a title page and the table of contents, with each chapter
   on a new page. The contents show the printed book's page numbers; in Word or LibreOffice,
   right-click the table and choose **Update Field** to use the RTF's own page numbers
   instead. **Text** downloads a plain-text copy.

### Tips for good scans

- Use the camera's highest resolution (KollinsScan asks for it automatically). A 4K or
  "document camera" gives clearly better results than a 720p webcam. Each letter should be at
  least ~20 pixels tall in the picture.
- Light the page evenly from above and avoid glare on glossy paper.
- Fill most of the picture with the page and keep it flat; hold a curved page flat with a
  sheet of clear glass or acrylic if needed.
- Scan **one page per picture**.

## Put it on a Hostinger VPS

You need a Hostinger VPS (**KVM 1** with 4 GB memory works; **KVM 2** reads pages faster) and a
domain or subdomain, e.g. `scan.yourdomain.com`. HTTPS is required, because browsers only allow
the camera on secure sites; it is set up automatically.

### 1. Set up the VPS

In **hPanel → VPS**, choose **Ubuntu 24.04 with Docker** as the operating system
(*OS & Panel → Operating System → Applications → Docker*). Plain Ubuntu 24.04 also works; then
install Docker with `curl -fsSL https://get.docker.com | sh` once you're signed in.

Note the VPS's **IP address** on its overview page.

### 2. Point your domain at it

In your domain's DNS settings (in hPanel: **Domains → your domain → DNS / Nameservers**), add an
**A record**: name `scan` (or `@` for the bare domain), pointing to the VPS IP address. It can
take a few minutes to start working.

### 3. Install KollinsScan

Sign in to the VPS (hPanel's **Browser terminal** button, or `ssh root@<VPS IP>` from your
computer), then run:

```bash
git clone https://github.com/rickkollins/KollinsScan.git /opt/kollinsscan
cd /opt/kollinsscan
cp .env.example .env
nano .env
```

The repository is private, so `git clone` asks you to sign in: use your GitHub user name and a
[personal access token](https://github.com/settings/tokens) as the password.

In `.env`, set at least:

- `KOLLINSSCAN_DOMAIN`: the domain from step 2, e.g. `scan.yourdomain.com`
- `KOLLINSSCAN_ADMIN_PASSWORD`: a long password (`openssl rand -base64 18` makes one)

Save with **Ctrl+O**, **Enter**, then **Ctrl+X**. Start it:

```bash
docker compose up -d --build
```

The first build takes a few minutes, and the grammar checker needs about a minute more to start.
Then open `https://scan.yourdomain.com` on the computer with the USB camera and sign in.

### 4. Firewall

If you use Hostinger's VPS firewall (hPanel → VPS → **Security → Firewall**), allow incoming
TCP **22** (SSH), **80** and **443**. Port 80 is needed for the HTTPS certificate.

## Everyday tasks (on the VPS, in `/opt/kollinsscan`)

| Task | Command |
|---|---|
| Update to the latest version | `git pull && docker compose up -d --build` |
| Change settings or password | `nano .env`, then `docker compose up -d` |
| See logs | `docker compose logs -f app` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Back up all books (text and photos) | `docker compose cp app:/data ./kollinsscan-backup` |

## Languages

English is installed by default. For books in other languages, list their
[Tesseract codes](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)
in `.env`, e.g. `TESSERACT_LANGS=eng spa fra deu`, and run `docker compose up -d --build`. Choose
the language when you create a book. Grammar checking supports English, Spanish, French, German,
Italian, Portuguese, Dutch, Polish, Russian, Ukrainian, Catalan, Danish, Swedish, Irish and
Greek.

## Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `KOLLINSSCAN_DOMAIN` | — | Domain for the site and its HTTPS certificate |
| `KOLLINSSCAN_ADMIN_USER` | `admin` | Sign-in user name |
| `KOLLINSSCAN_ADMIN_PASSWORD` | — | Sign-in password (no one can sign in until it's set) |
| `TESSERACT_LANGS` | `eng` | OCR languages to install |
| `KOLLINSSCAN_DEFAULT_LANGUAGE` | `eng` | Language preselected for new books |
| `KOLLINSSCAN_MAX_UPLOAD_MB` | `20` | Largest photo accepted |
| `KOLLINSSCAN_OCR_WORKERS` | `1` | Pages read at the same time (one CPU core each) |
| `KOLLINSSCAN_OCR_TIMEOUT` | `120` | Seconds before reading one page gives up |
| `LANGUAGETOOL_MEMORY` | `1g` | Memory for the grammar checker |
| `KOLLINSSCAN_LANGUAGETOOL_URL` | (built in) | Set it empty to turn grammar checking off |

## Run it on your own computer (development)

Needs Python 3.11+ and Tesseract (`brew install tesseract` on a Mac,
`sudo apt install tesseract-ocr` on Ubuntu). The camera works on `http://127.0.0.1` without
HTTPS.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
# Optional, for grammar checks:
docker run -d -p 8010:8010 erikvl87/languagetool:6.7
KOLLINSSCAN_ADMIN_PASSWORD=dev KOLLINSSCAN_SECURE_COOKIES=0 \
  KOLLINSSCAN_LANGUAGETOOL_URL=http://127.0.0.1:8010 uvicorn kollinsscan.app:app --reload
```

Open http://127.0.0.1:8000 and sign in as `admin` / `dev`.

Tests: `python -m unittest discover -s tests -v`

## How it's built

- `kollinsscan/app.py`: the web server ([Starlette](https://www.starlette.io/)) and its API
- `kollinsscan/ocr.py`: photo clean-up, Tesseract, paragraphs, headings and page numbers
- `kollinsscan/document.py`: the text model, HTML clean-up, table of contents, RTF and text export
- `kollinsscan/grammar.py`: LanguageTool checks
- `kollinsscan/static/`: the web page (plain HTML, CSS and JavaScript; the camera uses the
  browser's built-in `getUserMedia`)
- `Dockerfile`, `docker-compose.yml`, `Caddyfile`: the app, LanguageTool, and
  [Caddy](https://caddyserver.com/) in front for HTTPS

Books are stored in a SQLite database, and page photos as JPEG files, in the `kollinsscan-data`
Docker volume.

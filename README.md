# KollinsScan

Scan a book one page at a time into a single, editable document, proofread it, and download it
as an **RTF** or **Word (.docx)** file with a **table of contents**.

KollinsScan runs on your own server (for example a Hostinger VPS). You use it in a web browser on
the computer your **USB camera** is plugged into: the browser shows the camera, you press
**Space** for each page, and the server reads the text with the free
[Tesseract](https://github.com/tesseract-ocr/tesseract) OCR engine. Nothing is sent to any
outside service, except the pages you choose to **Ask Claude** about (optional, see below).

## What it does with each page

- **Reads the text** after evening out the lighting of the photo (dark gutters and shadows).
- **Rebuilds paragraphs** instead of one line per line, and rejoins words hyphenated at the
  end of a line (`for-` / `ward` → `forward`).
- **Fixes OCR punctuation slips** such as `word ,` → `word,` and `there.The` → `there. The`.
- **Finds the printed page number** and uses it as the page's number. Running headers (the
  book title or chapter name at the top of each page) are recognised and removed.
- **Finds chapter headings** (`Chapter 3`, `CHAPTER THREE`, `Part Two`, `Prologue`, a Roman
  numeral, a centred title...) for the table of contents.
- **Keeps italics and bold**: words printed in italic or bold are recognised from the shape of
  their letters and keep that formatting.
- **Highlights words in yellow** that the scanner wasn't sure about, so you know where to look.
- **Checks spelling, grammar and punctuation** with [LanguageTool](https://languagetool.org/)
  (also running on your server), and lists each problem with one-click fixes.

All pages appear as one continuous document you can edit directly. A sentence that runs from
one page onto the next is joined back into one paragraph in the downloads.

### Ask Claude (optional)

For hard pages (old or smudged print, unusual fonts, lots of yellow words), click **Ask Claude**
on the page. Claude (Anthropic's AI) reads the page's photo together with the current text and
proposes a corrected version, keeping italics, bold and headings. KollinsScan shows what would
change, word by word (red: yours, green: Claude's), and you click **Use this** or **Keep mine**.
Claude fixes scanning mistakes only; it doesn't rewrite the author's wording.

This needs an Anthropic API key (from [console.anthropic.com](https://console.anthropic.com/))
and costs roughly 1–4 US cents per page you ask about. Only those pages' photos and text are
sent to Anthropic. Without a key, the button doesn't appear.

## Using it

1. Sign in, enter the book's title and author, and click **Create and start scanning**.
2. Click **Turn on camera** and allow camera access when the browser asks. If you have more than
   one camera, pick the USB camera from the **Camera** list. If the picture is sideways (the
   camera is mounted on its side), click **Rotate** until the page reads upright.
   **Camera settings** has the resolution and, if your camera offers them, focus, exposure,
   brightness and zoom. Switch focus to **Manual** and adjust it once for a fixed book stand, so
   the camera doesn't refocus between pages. KollinsScan remembers these settings per camera.
   The **Sharpness** bar shows whether the picture is in focus (and "Moving…" while the page
   settles).
3. Put the first page under the camera and press **Space** (or click **Capture page**). Turn the
   page and press Space again. You don't have to wait: pages are read in order in the
   background. Each capture watches the camera for half a second and keeps the sharpest frame;
   if even that one is blurry, the page says so, so you can **Re-scan** it.
4. Fix anything you like right in the text. Changes save automatically.
   - **Chapter** / **Section** turn the line with the cursor into a heading for the table of
     contents; **¶ Text** turns it back into normal text. **B**, **I** and **U** format the
     selection.
   - **Photo** shows the page's photo beside its text, for checking yellow words.
   - **Ask Claude** re-reads the page (see above).
   - **Re-scan** captures that page again (to replace a blurry one); **Add after** scans a page
     you missed into the right place; ↑ ↓ reorder; ✕ deletes.
   - Click a page's number to correct it.
5. **Proofread** (right-hand panel): each new page is checked automatically, or click
   **Check whole book**. Click a problem to jump to it, then click a suggested fix, **Ignore**,
   **Add to dictionary** (for names and invented words in the book) or **Turn off this rule**.
6. **Download**: **RTF** or **Word**. Both start with a title page and the table of contents,
   with each chapter on a new page. In the Word file the contents are clickable links, and Word
   offers to update them to the document's own page numbers when it opens (click **Yes**). In
   the RTF they show the printed book's page numbers; right-click the table and choose
   **Update Field** to switch. **Text** downloads a plain-text copy.

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

### 1. Point your domain at the VPS

In your domain's DNS settings (in hPanel: **Domains → your domain → DNS / Nameservers**), add an
**A record**: name `@` (or e.g. `scan` for `scan.yourdomain.com`), pointing to the VPS's IP
address (shown on the VPS's overview page in hPanel). It can take a few minutes to work.

### 2. Install

Open a terminal on the VPS: hPanel → **VPS → Manage → Browser terminal**, or `ssh root@<VPS IP>`
from your computer. The operating system should be Ubuntu 24.04 or Debian 12 (Hostinger's
"Ubuntu 24.04 with Docker" image is ideal, but plain Ubuntu works too). Then run this one command:

```bash
git clone https://github.com/rickkollins/KollinsScan.git /opt/kollinsscan && /opt/kollinsscan/install.sh
```

The repository is private, so `git clone` asks you to sign in: use your GitHub user name and, as
the password, a [fine-grained token](https://github.com/settings/personal-access-tokens) with
read-only **Contents** access to KollinsScan.

The installer asks for your domain and (optionally) an Anthropic API key, then does the rest:
installs Docker if needed, checks that the domain points to this server, creates a sign-in
password, sizes the OCR workers and grammar checker to the server, opens the firewall ports,
builds and starts everything, and waits for HTTPS to come up. At the end it prints the address,
user name and password. The first run takes a few minutes.

You can also pass everything up front:
`/opt/kollinsscan/install.sh --domain scan.yourdomain.com --api-key sk-ant-... --yes`

If you use Hostinger's VPS firewall (hPanel → VPS → **Security → Firewall**), allow incoming
TCP **22** (SSH), **80** and **443**. Port 80 is needed for the HTTPS certificate.

## Managing it

The installer adds a `kollinsscan` command on the VPS:

| Command | What it does |
|---|---|
| `kollinsscan update` | Get the latest version and restart (asks for the GitHub token) |
| `kollinsscan status` | Show whether everything is running |
| `kollinsscan logs` | Follow the logs (`kollinsscan logs caddy` for HTTPS problems) |
| `kollinsscan password` | Set a new sign-in password (or generate one) |
| `kollinsscan api-key` | Set or remove the Anthropic API key for Ask Claude |
| `kollinsscan backup` | Save all books and photos to a `.tar.gz` file |
| `kollinsscan restore FILE` | Replace all books and photos with a backup |
| `kollinsscan uninstall` | Remove KollinsScan and all its data |

To change any other setting, edit `/opt/kollinsscan/.env` and run
`cd /opt/kollinsscan && docker compose up -d`.

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
| `KOLLINSSCAN_OCR_WORKERS` | `1` (installer: CPU cores) | Pages read at the same time (one CPU core each) |
| `KOLLINSSCAN_OCR_TIMEOUT` | `120` | Seconds before reading one page gives up |
| `LANGUAGETOOL_MEMORY` | `1g` | Memory for the grammar checker |
| `KOLLINSSCAN_LANGUAGETOOL_URL` | (built in) | Set it empty to turn grammar checking off |
| `ANTHROPIC_API_KEY` | — | Turns on Ask Claude |
| `KOLLINSSCAN_CLAUDE_MODEL` | `claude-opus-5-5` | Claude model used by Ask Claude |

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
- `kollinsscan/ocr.py`: photo clean-up, Tesseract, paragraphs, headings, page numbers, italics
  and bold
- `kollinsscan/document.py`: the text model, HTML clean-up, table of contents, RTF and text export
- `kollinsscan/docx_export.py`: Word export
- `kollinsscan/grammar.py`: LanguageTool checks
- `kollinsscan/claude.py`: Ask Claude
- `install.sh`: the installer and the `kollinsscan` command
- `kollinsscan/static/`: the web page (plain HTML, CSS and JavaScript; the camera uses the
  browser's built-in `getUserMedia`)
- `Dockerfile`, `docker-compose.yml`, `Caddyfile`: the app, LanguageTool, and
  [Caddy](https://caddyserver.com/) in front for HTTPS

Books are stored in a SQLite database, and page photos as JPEG files, in the `kollinsscan-data`
Docker volume.

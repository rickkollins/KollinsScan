# KollinsScan

A private, self-hosted OCR web app. Sign in, drop in images (or paste a screenshot), and get
the text back to copy or download as a `.txt` file. It runs on your own server using the free
[Tesseract](https://github.com/tesseract-ocr/tesseract) engine, so your images never leave it.

- Images: JPG, PNG, TIFF (multi-page too), BMP, GIF and WebP, up to 20 MB each.
- Several files at once, drag and drop, or paste from the clipboard (Ctrl/⌘ V).
- Phone photos are straightened automatically, and small images are enlarged before reading.
- **History** keeps the text of everything you've read, so you can open, re-download or delete
  it later. Only the text is kept; uploaded images are never saved.
- One password-protected account, HTTPS with an automatic certificate, and sign-in lockout
  after 10 wrong passwords.

## Put it on a Hostinger VPS

You need a Hostinger VPS (the smallest **KVM 1** plan is enough) and a domain or subdomain,
e.g. `scan.yourdomain.com`.

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

The first build takes a few minutes. Then open `https://scan.yourdomain.com` and sign in.

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
| Back up the history | `docker compose cp app:/data/kollinsscan.db ./kollinsscan-backup.db` |

## Languages

English is installed by default. To read other languages, list their
[Tesseract codes](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)
in `.env`, e.g. `TESSERACT_LANGS=eng spa fra deu`, and run `docker compose up -d --build`. Pick the
language from the menu at the top of the page. To read a document that mixes two languages,
set `KOLLINSSCAN_DEFAULT_LANGUAGE=eng+spa`.

## Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `KOLLINSSCAN_DOMAIN` | — | Domain for the site and its HTTPS certificate |
| `KOLLINSSCAN_ADMIN_USER` | `admin` | Sign-in user name |
| `KOLLINSSCAN_ADMIN_PASSWORD` | — | Sign-in password (no one can sign in until it's set) |
| `TESSERACT_LANGS` | `eng` | OCR languages to install |
| `KOLLINSSCAN_DEFAULT_LANGUAGE` | `eng` | Language selected at first |
| `KOLLINSSCAN_MAX_UPLOAD_MB` | `20` | Largest image you can upload |
| `KOLLINSSCAN_OCR_WORKERS` | `2` | Images read at the same time (one CPU core each) |
| `KOLLINSSCAN_OCR_TIMEOUT` | `120` | Seconds before a single page gives up |

## Run it on your own computer (development)

Needs Python 3.11+ and Tesseract (`brew install tesseract` on a Mac,
`sudo apt install tesseract-ocr` on Ubuntu).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
KOLLINSSCAN_ADMIN_PASSWORD=dev KOLLINSSCAN_SECURE_COOKIES=0 uvicorn kollinsscan.app:app --reload
```

Open http://127.0.0.1:8000 and sign in as `admin` / `dev`.

Tests: `python -m unittest discover -s tests -v`

## How it's built

- `kollinsscan/app.py`: the web server ([Starlette](https://www.starlette.io/)) and its API
- `kollinsscan/ocr.py`: image clean-up and Tesseract
- `kollinsscan/static/`: the web page (plain HTML, CSS and JavaScript)
- `Dockerfile`, `docker-compose.yml`, `Caddyfile`: the app container, plus
  [Caddy](https://caddyserver.com/) in front of it for HTTPS

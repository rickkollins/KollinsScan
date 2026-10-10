#!/usr/bin/env bash
# KollinsScan installer and manager for an Ubuntu or Debian VPS.
#
# Install (one command, as root):
#   git clone https://github.com/rickkollins/KollinsScan.git /opt/kollinsscan && /opt/kollinsscan/install.sh
#
# Afterwards, manage it with the `kollinsscan` command:
#   kollinsscan update            get the latest version and restart
#   kollinsscan status            is everything running?
#   kollinsscan logs [service]    follow the logs (app, languagetool, caddy)
#   kollinsscan password [new]    set a new sign-in password
#   kollinsscan api-key [key]     set (or clear) the Anthropic API key for "Ask Claude"
#   kollinsscan backup [file]     save all books and photos to a .tar.gz file
#   kollinsscan restore <file>    replace all books and photos with a backup
#   kollinsscan uninstall         remove KollinsScan and all its data
#
# Install options (otherwise it asks): --domain NAME, --password PW,
# --api-key KEY, --skip-dns-check, --no-wait, --yes (accept defaults).

set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$DIR/.env"
BIN_DIR="${KOLLINSSCAN_BIN_DIR:-/usr/local/bin}"
COMPOSE=(docker compose --project-directory "$DIR" -f "$DIR/docker-compose.yml")

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

need_root() {
    if [[ $EUID -ne 0 && -z "${KOLLINSSCAN_ALLOW_NONROOT:-}" ]]; then
        die "run this as root (or with sudo)."
    fi
}

# Read a value from the user's terminal, even when this script is piped.
ask() {  # ask VAR "Question" [default] [secret]
    local __var=$1 question=$2 default=${3:-} secret=${4:-} reply=""
    if [[ -n "${ASSUME_YES:-}" || ! -r /dev/tty ]]; then
        printf -v "$__var" '%s' "$default"
        return
    fi
    local prompt="$question"
    [[ -n "$default" && -z "$secret" ]] && prompt+=" [$default]"
    if [[ -n "$secret" ]]; then
        read -r -s -p "$prompt: " reply </dev/tty || true
        echo
    else
        read -r -p "$prompt: " reply </dev/tty || true
    fi
    printf -v "$__var" '%s' "${reply:-$default}"
}

env_get() {  # env_get KEY -> value from .env (empty if missing)
    [[ -f "$ENV_FILE" ]] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

env_set() {  # env_set KEY VALUE
    local key=$1 value=$2
    [[ "$value" != *$'\n'* && "$value" != *"|"* ]] || die "$key contains a character that isn't allowed."
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    if grep -q "^$key=" "$ENV_FILE"; then
        sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
    elif grep -q "^# *$key=" "$ENV_FILE"; then
        sed -i "0,/^# *$key=.*/s||$key=$value|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
    fi
}

new_password() {
    openssl rand -hex 12 2>/dev/null || tr -dc 'a-z0-9' </dev/urandom | head -c 24
}

ensure_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        bold "Installing Docker..."
        command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
        curl -fsSL https://get.docker.com | sh
    fi
    docker compose version >/dev/null 2>&1 \
        || die "Docker is installed but 'docker compose' isn't. Install the docker-compose-plugin package."
}

public_ip() {
    curl -fsS -4 --max-time 10 https://api.ipify.org 2>/dev/null \
        || curl -fsS -4 --max-time 10 https://ifconfig.me 2>/dev/null || true
}

check_dns() {  # check_dns DOMAIN
    local domain=$1 mine theirs
    mine=$(public_ip)
    theirs=$(getent ahostsv4 "$domain" 2>/dev/null | awk 'NR==1 {print $1}')
    if [[ -z "$theirs" ]]; then
        warn "$domain doesn't resolve to any address yet."
        local target=${mine:-"this server's IP address"}
        info "Add an A record for $domain pointing to $target, then wait a few minutes."
        return 1
    fi
    if [[ -n "$mine" && "$theirs" != "$mine" ]]; then
        warn "$domain points to $theirs, but this server is $mine."
        info "Change the domain's A record to $mine."
        return 1
    fi
    info "$domain points to this server ($theirs)."
}

open_firewall() {
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 22/tcp >/dev/null
        ufw allow 80/tcp >/dev/null
        ufw allow 443/tcp >/dev/null
        ufw allow 443/udp >/dev/null
        info "Firewall (ufw): opened ports 80 and 443."
    fi
}

wait_until_up() {  # wait_until_up DOMAIN
    local domain=$1
    bold "Waiting for https://$domain to come up (the HTTPS certificate takes a minute)..."
    for _ in $(seq 1 60); do
        if curl -fsS --max-time 5 "https://$domain/healthz" >/dev/null 2>&1; then
            info "It's up."
            return 0
        fi
        sleep 5
    done
    warn "https://$domain isn't answering yet."
    info "If the domain was only just pointed here, give it a few minutes. To see why: kollinsscan logs caddy"
    return 1
}

link_command() {
    mkdir -p "$BIN_DIR"
    ln -sf "$DIR/install.sh" "$BIN_DIR/kollinsscan"
}

restart_app() {
    "${COMPOSE[@]}" up -d app >/dev/null
}

# ---------------------------------------------------------------------------

cmd_install() {
    local domain="" password="" api_key="" skip_dns="" no_wait="" generated=""
    local api_key_given=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --domain) domain=$2; shift 2 ;;
            --password) password=$2; shift 2 ;;
            --api-key) api_key=$2; api_key_given=1; shift 2 ;;
            --skip-dns-check) skip_dns=1; shift ;;
            --no-wait) no_wait=1; shift ;;
            --yes|-y) ASSUME_YES=1; shift ;;
            *) die "unknown option: $1" ;;
        esac
    done
    need_root
    bold "KollinsScan setup"
    ensure_docker
    [[ -f "$ENV_FILE" ]] || cp "$DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    # Domain
    local current_domain
    current_domain=$(env_get KOLLINSSCAN_DOMAIN)
    [[ "$current_domain" == "scan.example.com" ]] && current_domain=""
    [[ -n "$domain" ]] || ask domain "Domain for KollinsScan (e.g. scan.example.com)" "$current_domain"
    domain=${domain#http://}; domain=${domain#https://}; domain=${domain%%/*}
    [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$ ]] \
        || die "'$domain' isn't a domain name. KollinsScan needs one for HTTPS (the camera only works on HTTPS sites)."
    env_set KOLLINSSCAN_DOMAIN "$domain"
    if [[ -z "$skip_dns" ]]; then
        if ! check_dns "$domain"; then
            local go_on
            ask go_on "Continue anyway? HTTPS starts working once the domain points here (y/N)" "y"
            [[ "$go_on" =~ ^[Yy] ]] || exit 1
        fi
    fi

    # Password: keep an existing one unless a new one was given.
    if [[ -n "$password" ]]; then
        env_set KOLLINSSCAN_ADMIN_PASSWORD "$password"
    elif [[ -z "$(env_get KOLLINSSCAN_ADMIN_PASSWORD)" ]]; then
        password=$(new_password)
        generated=1
        env_set KOLLINSSCAN_ADMIN_PASSWORD "$password"
    fi

    # Anthropic API key (optional)
    if [[ -z "$api_key_given" && -z "$(env_get ANTHROPIC_API_KEY)" ]]; then
        ask api_key "Anthropic API key for \"Ask Claude\" on hard pages (Enter to skip)" "" secret
    fi
    if [[ -n "$api_key" || -n "$api_key_given" ]]; then
        env_set ANTHROPIC_API_KEY "$api_key"
    fi

    # Size things to this server.
    local cores mem_mb
    cores=$(nproc 2>/dev/null || echo 1)
    env_set KOLLINSSCAN_OCR_WORKERS "$cores"
    mem_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || echo 4096)
    if (( mem_mb < 3000 )); then
        env_set LANGUAGETOOL_MEMORY 512m
        warn "This server has only ${mem_mb} MB of memory; the grammar checker gets 512 MB."
    fi

    open_firewall
    link_command

    bold "Building and starting KollinsScan (the first time takes a few minutes)..."
    "${COMPOSE[@]}" up -d --build

    [[ -n "$no_wait" ]] || wait_until_up "$domain" || true

    echo
    bold "KollinsScan is installed."
    info "Open:      https://$domain"
    info "User name: $(env_get KOLLINSSCAN_ADMIN_USER)"
    if [[ -n "$generated" ]]; then
        info "Password:  $password   (save it; change it with: kollinsscan password)"
    else
        info "Password:  (unchanged; set a new one with: kollinsscan password)"
    fi
    if [[ -n "$(env_get ANTHROPIC_API_KEY)" ]]; then
        info "Ask Claude: on"
    else
        info "Ask Claude: off (turn on with: kollinsscan api-key)"
    fi
    info "Manage it with: kollinsscan update | status | logs | backup | password"
}

cmd_update() {
    need_root
    bold "Updating KollinsScan..."
    git -C "$DIR" pull --ff-only
    "${COMPOSE[@]}" up -d --build
    docker image prune -f >/dev/null
    info "Updated. $(git -C "$DIR" log -1 --format='Now at %h: %s')"
}

cmd_status() {
    "${COMPOSE[@]}" ps
    local domain
    domain=$(env_get KOLLINSSCAN_DOMAIN)
    if curl -fsS --max-time 10 "https://$domain/healthz" >/dev/null 2>&1; then
        info "https://$domain is up."
    else
        warn "https://$domain isn't answering. See: kollinsscan logs"
    fi
}

cmd_logs() {
    "${COMPOSE[@]}" logs -f --tail 100 "$@"
}

cmd_password() {
    need_root
    local password=${1:-}
    if [[ -z "$password" ]]; then
        ask password "New password (Enter to generate one)" "" secret
    fi
    local shown=""
    if [[ -z "$password" ]]; then
        password=$(new_password)
        shown=1
    fi
    env_set KOLLINSSCAN_ADMIN_PASSWORD "$password"
    restart_app
    if [[ -n "$shown" ]]; then
        info "New password: $password"
    else
        info "Password changed."
    fi
}

cmd_api_key() {
    need_root
    local key=${1-__ask__}
    if [[ "$key" == "__ask__" ]]; then
        ask key "Anthropic API key (Enter to turn Ask Claude off)" "" secret
    fi
    env_set ANTHROPIC_API_KEY "$key"
    restart_app
    if [[ -n "$key" ]]; then info "Ask Claude is on."; else info "Ask Claude is off."; fi
}

cmd_backup() {
    local file=${1:-"$HOME/kollinsscan-backup-$(date +%Y%m%d-%H%M%S).tar.gz"}
    "${COMPOSE[@]}" exec -T app tar -C /data -czf - . >"$file"
    info "Saved $(du -h "$file" | cut -f1) to $file"
}

cmd_restore() {
    need_root
    local file=${1:-}
    [[ -f "$file" ]] || die "usage: kollinsscan restore <backup.tar.gz>"
    local sure
    ask sure "This replaces ALL books and photos with the backup. Type yes to continue" ""
    [[ "$sure" == "yes" ]] || die "cancelled."
    "${COMPOSE[@]}" stop app >/dev/null
    "${COMPOSE[@]}" run --rm -T --no-deps --entrypoint sh app \
        -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' <"$file"
    "${COMPOSE[@]}" up -d app >/dev/null
    info "Restored from $file"
}

cmd_uninstall() {
    need_root
    local sure
    ask sure "This deletes KollinsScan and ALL books and photos. Type yes to continue" ""
    [[ "$sure" == "yes" ]] || die "cancelled."
    "${COMPOSE[@]}" down -v --rmi local
    rm -f "$BIN_DIR/kollinsscan"
    info "KollinsScan is removed. Delete its folder too with: rm -rf $DIR"
}

main() {
    local cmd=${1:-install}
    [[ $# -gt 0 ]] && shift
    case $cmd in
        install) cmd_install "$@" ;;
        update) cmd_update ;;
        status) cmd_status ;;
        logs) cmd_logs "$@" ;;
        password) cmd_password "$@" ;;
        api-key) cmd_api_key "$@" ;;
        backup) cmd_backup "$@" ;;
        restore) cmd_restore "$@" ;;
        uninstall) cmd_uninstall ;;
        -h|--help|help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
        --*) cmd_install "$cmd" "$@" ;;
        *) die "unknown command: $cmd (try: kollinsscan help)" ;;
    esac
}

main "$@"

#!/usr/bin/env python3
"""Link Download Bot for Telegram (v1).

Send the bot a link -> it downloads the file -> sends it back in the chat.
- Direct file links: streamed download, size shown before sending.
- Video/page links (YouTube, Instagram, X, etc.): handled by yt-dlp,
  best quality that fits Telegram's 50 MB bot limit.
- Public links only. No logins, no paywalls, no DRM.

Config: BOT_TOKEN env var or token.txt next to this file.
Optional: ALLOWED_IDS env var (comma-separated Telegram user IDs).
Run: python3 bot.py   (long-polling, no public port needed)
"""
import os, re, sys, json, time, glob, shutil, subprocess, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "pylibs"))
DL_DIR = os.path.join(BASE, "downloads")
os.makedirs(DL_DIR, exist_ok=True)

import requests

MAX_SEND = 48 * 1024 * 1024          # stay safely under Telegram's 50 MB bot limit
TG_LIMIT_MB = 50
URL_RE = re.compile(r"https?://[^\s]+")
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav"}
UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"}


def get_token():
    tok = os.environ.get("BOT_TOKEN", "").strip()
    if tok:
        return tok
    p = os.path.join(BASE, "token.txt")
    if os.path.exists(p):
        return open(p).read().strip()
    sys.exit("No BOT_TOKEN env var and no token.txt — cannot start.")


TOKEN = get_token()
API = f"https://api.telegram.org/bot{TOKEN}"
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_IDS", "").replace(" ", "").split(",") if x}


def tg(method, **kwargs):
    try:
        if "files" in kwargs:
            r = requests.post(f"{API}/{method}", data=kwargs.get("data", {}),
                              files=kwargs["files"], timeout=600)
        else:
            r = requests.post(f"{API}/{method}", json=kwargs.get("json", {}), timeout=60)
        return r.json()
    except Exception as e:
        print(f"[tg] {method} failed: {e}", flush=True)
        return {"ok": False, "description": str(e)}


def send_msg(chat_id, text, reply_to=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return tg("sendMessage", json=payload)


def fmt_size(n):
    if n is None:
        return "unknown size"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0


def safe_name(name):
    name = urllib.parse.unquote(name or "file")
    name = re.sub(r"[^\w.\- ]+", "_", name).strip()
    return name[:120] or "file"


# ---------------- downloaders ----------------

def download_direct(url, status):
    """Stream a direct file. Returns (path, size) or raises."""
    with requests.get(url, headers=UA, stream=True, timeout=30,
                      allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
        name = safe_name(m.group(1)) if m else safe_name(
            os.path.basename(urllib.parse.urlparse(r.url).path) or "download")
        if total and total > MAX_SEND:
            return None, total, name
        path = os.path.join(DL_DIR, f"{int(time.time())}_{name}")
        got = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if chunk:
                    f.write(chunk)
                    got += len(chunk)
                    if got > MAX_SEND:      # lied about size — stop
                        f.close()
                        os.remove(path)
                        return None, got, name
        return path, got, name


def download_ytdlp(url, status):
    """Use yt-dlp for video/page links. Returns (path, size, title)."""
    import yt_dlp
    out_tmpl = os.path.join(DL_DIR, f"{int(time.time())}_%(title).80s.%(ext)s")
    opts = {
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True, "no_warnings": True,
        # NOTE: many sites (Pinterest etc.) report no filesize, so never
        # filter on filesize — height + fallbacks only; size checked after.
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "merge_output_format": "mp4",
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
    }
    try:  # browser impersonation — Meta/Pinterest cut plain bot TLS
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts["impersonate"] = ImpersonateTarget(client="chrome")
    except Exception:
        pass
    before = set(glob.glob(DL_DIR + "/*"))
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "video")
    after = [p for p in glob.glob(DL_DIR + "/*") if p not in before]
    if not after:
        raise RuntimeError("yt-dlp produced no file")
    path = max(after, key=os.path.getsize)
    return path, os.path.getsize(path), title


def download_pagescan(url):
    """Fallback: fetch the page like a browser and pull the media URL out
    of it (og:video / embedded .mp4). Instagram goes via ddinstagram,
    a public embed proxy, because Meta truncates direct page fetches."""
    pages = [url]
    if "instagram.com" in url:
        pages.insert(0, url.replace("instagram.com", "ddinstagram.com")
                           .split("?")[0])
    found = []
    for page in pages:
        try:
            r = requests.get(page, headers=UA, timeout=25)
            html = r.text.replace("&amp;", "&")
        except Exception:
            continue
        for pat in (r'property="og:video(?::secure_url)?" content="([^"]+)"',
                    r'name="twitter:player:stream" content="([^"]+)"',
                    r'(https://[^\s"\'<>]+\.mp4[^\s"\'<>]*)'):
            for m in re.findall(pat, html):
                if m not in found:
                    found.append(m)
        if found:
            break
    best = None
    for cand in found[:8]:
        try:
            h = requests.head(cand, headers=UA, timeout=15,
                              allow_redirects=True)
            size = int(h.headers.get("Content-Length") or 0)
        except Exception:
            size = 0
        if size and size <= MAX_SEND and (best is None or size > best[1]):
            best = (cand, size)
        elif not size and best is None:
            best = (cand, 0)
    if not best:
        raise RuntimeError("page scan found no usable media URL")
    path, size, name = download_direct(best[0], None)
    if path is None:
        raise RuntimeError("page scan media over the size limit")
    return path, size, name


def looks_direct(url):
    path = urllib.parse.urlparse(url).path.lower()
    return any(path.endswith(e) for e in
               VIDEO_EXTS | AUDIO_EXTS | {".zip", ".rar", ".7z", ".pdf", ".apk",
                                          ".jpg", ".jpeg", ".png", ".gif", ".webp", ".doc", ".docx"})


# ---------------- message handling ----------------

def resolve_url(url):
    """Follow redirects (pin.it, bit.ly, etc.) to the real URL first —
    extractors fail on shortener hops, requests handles them cleanly."""
    try:
        r = requests.get(url, headers=UA, allow_redirects=True,
                         timeout=20, stream=True)
        final = r.url
        r.close()
        return final
    except Exception:
        return url


def handle_link(chat_id, msg_id, url):
    status = send_msg(chat_id, "Fetching your link…", reply_to=msg_id)
    try:
        url = resolve_url(url)
        if looks_direct(url):
            path, size, name = download_direct(url, status)
            if path is None:
                send_msg(chat_id,
                         f"That file is {fmt_size(size)} — over Telegram's "
                         f"{TG_LIMIT_MB} MB bot limit, so I can't send it back here.",
                         reply_to=msg_id)
                return
            send_msg(chat_id, f"Got it: {name} ({fmt_size(size)}). Uploading…",
                     reply_to=msg_id)
            send_file(chat_id, path, msg_id)
            os.remove(path)
        else:
            try:
                path, size, title = download_ytdlp(url, status)
            except Exception as e1:
                print(f"[dl] yt-dlp failed ({e1}); trying page scan", flush=True)
                path, size, title = download_pagescan(url)
            if size > MAX_SEND:
                os.remove(path)
                send_msg(chat_id,
                         f"That one is {fmt_size(size)} — over Telegram's "
                         f"{TG_LIMIT_MB} MB bot limit, so I can't send it here.",
                         reply_to=msg_id)
                return
            send_msg(chat_id, f"Got it: {title} ({fmt_size(size)}). Uploading…",
                     reply_to=msg_id)
            send_file(chat_id, path, msg_id, caption=title)
            os.remove(path)
    except Exception as e:
        print(f"[dl] {url} -> {e}", flush=True)
        send_msg(chat_id,
                 "Couldn't fetch that one. It may need a login, be blocked, "
                 "or be larger than 50 MB. Send me another link and I'll try.",
                 reply_to=msg_id)
        for p in glob.glob(DL_DIR + f"/{int(time.time()) - 5}*"):
            pass  # leftovers are cleaned by the sweeper in main loop


def send_file(chat_id, path, reply_to=None, caption=None):
    ext = os.path.splitext(path)[1].lower()
    method = "sendVideo" if ext in VIDEO_EXTS else (
        "sendAudio" if ext in AUDIO_EXTS else "sendDocument")
    data = {"chat_id": str(chat_id)}
    if reply_to:
        data["reply_to_message_id"] = str(reply_to)
    if caption:
        data["caption"] = caption[:1000]
    if method == "sendVideo":
        data["supports_streaming"] = "true"
    with open(path, "rb") as f:
        res = tg(method, data=data, files={"video" if method == "sendVideo" else
                                          "audio" if method == "sendAudio" else
                                          "document": (os.path.basename(path), f)})
    if not res.get("ok"):
        print(f"[send] {method} failed: {res}", flush=True)
        # fall back to document once
        if method != "sendDocument":
            with open(path, "rb") as f:
                tg("sendDocument", data={"chat_id": str(chat_id)},
                   files={"document": (os.path.basename(path), f)})


WELCOME = ("Send me a link and I'll download it and send the file back here.\n\n"
           "• Direct file links — any file up to 50 MB\n"
           "• Video links (YouTube, Instagram, X and most sites) — "
           "best quality that fits 50 MB\n\n"
           "Public links only: no logins, no paywalls.\n"
           "Commands: /id — your Telegram ID, /help — this message")


def main():
    me = tg("getMe").get("result", {})
    print(f"Bot live: @{me.get('username')} (id {me.get('id')})", flush=True)
    tg("deleteWebhook", json={})
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 50},
                             timeout=70)
            data = r.json()
            # hourly sweep of stale downloads
            for p in glob.glob(DL_DIR + "/*"):
                if time.time() - os.path.getmtime(p) > 3600:
                    try: os.remove(p)
                    except OSError: pass
            if not data.get("ok"):
                time.sleep(3); continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                user_id = (msg.get("from") or {}).get("id")
                text = msg.get("text") or ""
                if chat_id is None:
                    continue
                if ALLOWED and user_id not in ALLOWED:
                    send_msg(chat_id, "This is a private bot.")
                    continue
                if text.startswith("/start") or text.startswith("/help"):
                    send_msg(chat_id, WELCOME)
                elif text.startswith("/id"):
                    send_msg(chat_id, f"Your user ID: {user_id}\nThis chat ID: {chat_id}")
                else:
                    m = URL_RE.search(text)
                    if m:
                        handle_link(chat_id, msg.get("message_id"), m.group(0))
                    elif text:
                        send_msg(chat_id, "Send me a link and I'll fetch the file for you.")
        except Exception as e:
            print(f"[loop] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()

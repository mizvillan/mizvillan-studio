#!/usr/bin/env python3
"""
MIZVILLAN STUDIO — local AI server
----------------------------------
Serves the studio at http://localhost:8787 and proxies AI calls with your
API keys read from your bots' .env files. Keys NEVER leave this machine —
they're injected server-side and never printed anywhere.

Run:  py studio-server.py
Then open http://localhost:8787
"""
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8787
ROOT = os.path.dirname(os.path.abspath(__file__))
# Groq sits behind Cloudflare and 403s (error 1010) anything that looks like a
# scripting agent, so every upstream request has to claim to be a browser.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SFX_DIR = os.path.join(ROOT, "sfx")
os.makedirs(SFX_DIR, exist_ok=True)  # user drops meme sounds here (vine boom etc.)

# ── youtube link pull ────────────────────────────────────────────────
# paste a link → yt-dlp grabs the video → the browser loads it → clip hunter
# turns it into shorts. Files land in _yt/ (gitignored) and are served statically.
try:
    import yt_dlp
except Exception:  # noqa: BLE001
    yt_dlp = None

def _find_ffmpeg():
    # yt-dlp has to mux the separate video+audio streams youtube hands out,
    # so we ship a static ffmpeg through imageio-ffmpeg if there isnt one.
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")

FFMPEG = _find_ffmpeg()
YT_DIR = os.path.join(ROOT, "_yt")
os.makedirs(YT_DIR, exist_ok=True)
YT_MAX_SECS = 30 * 60        # the browser has to hold the video + audio in RAM
YT_MAX_SECS_AUDIO = 6 * 3600  # audio is tiny — a whole podcast is fine
YT_MAX_FILES = 6             # keep the pull cache tidy
YT_JOBS = {}
YT_LOCK = threading.Lock()

YT_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"

def _yt_set(job, **kw):
    with YT_LOCK:
        job.update(kw)

def _yt_purge_cache():
    try:
        entries = [(os.path.getmtime(os.path.join(YT_DIR, f)), f) for f in os.listdir(YT_DIR)]
    except OSError:
        return
    entries.sort(reverse=True)
    for _, name in entries[YT_MAX_FILES:]:
        try:
            os.remove(os.path.join(YT_DIR, name))
        except OSError:
            pass

def _yt_msg(err):
    """yt-dlp errors are a wall of text — keep the first human line."""
    line = str(err).strip().splitlines()
    line = next((l for l in line if l.strip()), "download failed")
    line = line.replace("ERROR:", "").replace("WARNING:", "").strip()
    if "Sign in to confirm" in line:
        return "youtube wants a login for this one — try another link"
    if "Video unavailable" in line:
        return "that video is unavailable (private / region locked?)"
    if "Private video" in line:
        return "that video is private"
    return line[:300]

def _yt_existing(vid, kind):
    """Cached pull for this video id, if the file is still around."""
    try:
        names = os.listdir(YT_DIR)
    except OSError:
        return None
    # audio pulls are saved as <id>.audio.<ext> so they never shadow a video
    want = vid + ".audio" if kind == "audio" else vid
    exts = (".mp3", ".m4a", ".opus", ".aac", ".wav", ".webm") if kind == "audio" else (".mp4", ".mkv", ".webm")
    for f in names:
        stem, ext = os.path.splitext(f)
        if stem == want and ext.lower() in exts:
            path = os.path.join(YT_DIR, f)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return f
    return None

def yt_worker(job, url, kind="video"):
    os.makedirs(YT_DIR, exist_ok=True)
    _yt_purge_cache()
    if yt_dlp is None:
        return _yt_set(job, status="error",
                       error="yt-dlp isnt installed — run: py -m pip install yt-dlp")
    if not FFMPEG:
        return _yt_set(job, status="error",
                       error="ffmpeg missing — run: py -m pip install imageio-ffmpeg")

    audio = kind == "audio"
    opts = dict(
        quiet=True, no_warnings=True, noprogress=True, noplaylist=True,
        ffmpeg_location=FFMPEG,
        format="bestaudio/best" if audio else YT_FORMAT,
        merge_output_format=None if audio else "mp4",
        outtmpl=os.path.join(
            YT_DIR,
            "%(id)s.audio.%(ext)s" if audio else "%(id)s.%(ext)s",
        ),
    )
    if audio:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    max_secs = YT_MAX_SECS_AUDIO if audio else YT_MAX_SECS
    try:
        _yt_set(job, status="meta", pct=3)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("_type") in ("playlist", "multi_video"):
                return _yt_set(job, status="error",
                               error="thats a playlist — paste a single video/shorts link")
            vid = info.get("id") or ""
            title = (info.get("title") or "youtube video").strip()
            dur = int(info.get("duration") or 0)
            if not vid or not dur:
                return _yt_set(job, status="error",
                               error="couldnt read that link — is it a normal youtube url?")
            if dur > max_secs:
                cap = (YT_MAX_SECS_AUDIO // 3600) if audio else (YT_MAX_SECS // 60)
                unit = "hours" if audio else "min"
                return _yt_set(job, status="error",
                               error=(f"that video is {dur // (3600 if audio else 60)} "
                                      f"{'hours' if audio else 'min'} — {kind} extract caps at "
                                      f"{cap} {unit}. drop your own file for longer stuff."))
            _yt_set(job, title=title, duration=dur, kind=kind)

            hit = _yt_existing(vid, kind)
            if not hit:
                def hook(d):
                    st = d.get("status")
                    if st == "downloading":
                        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        done = d.get("downloaded_bytes") or 0
                        pct = int(done * 100 / total) if total else 0
                        _yt_set(job, status="downloading", pct=min(pct, 93))
                    elif st in ("finished", "processing"):
                        _yt_set(job, status="merging", pct=95)
                opts["progress_hooks"] = [hook]
                with yt_dlp.YoutubeDL(opts) as ydl2:
                    ydl2.extract_info(url, download=True)
                hit = _yt_existing(vid, kind)
            if not hit:
                return _yt_set(job, status="error", error="download finished but no file showed up")
    except Exception as e:  # noqa: BLE001
        return _yt_set(job, status="error", error=_yt_msg(e))

    path = os.path.join(YT_DIR, hit)
    _yt_set(job, status="done", pct=100, file="/_yt/" + hit, size=os.path.getsize(path))
    print(f"[studio] pulled {kind} {title[:50]!r} -> {hit} ({os.path.getsize(path) // 1024} KB)",
          file=sys.stderr)

# .env files to mine for keys (siblings of this folder)
ENV_CANDIDATES = [
    os.path.join(ROOT, "..", "Kyronator", ".env"),
    os.path.join(ROOT, "..", "Mizvillan's bot", ".env"),
]

KEY_NAMES = [
    "GROQ_API_KEY", "GROQ_API_KEYS",
    "GEMINI_API_KEY", "GEMINI_API_KEYS",
    "OPENROUTER_API_KEY", "OPENROUTER_API_KEYS",
    "MISTRAL_API_KEY", "MISTRAL_API_KEYS",
    "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID",
]

def load_keys():
    found = {}
    for path in ENV_CANDIDATES:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, value = line.partition("=")
                    name = name.strip()
                    value = value.strip().strip('"').strip("'")
                    if name in KEY_NAMES and value:
                        found[name] = value
        except OSError:
            continue
    keys = {}
    # multi-key lists first, then singles
    def add(dest, multi, single):
        out = []
        if multi and found.get(multi):
            out += [k.strip() for k in found[multi].split(",") if k.strip()]
        if single and found.get(single):
            if found[single] not in out:
                out.append(found[single])
        keys[dest] = out
    add("groq", "GROQ_API_KEYS", "GROQ_API_KEY")
    add("gemini", "GEMINI_API_KEYS", "GEMINI_API_KEY")
    add("openrouter", "OPENROUTER_API_KEYS", "OPENROUTER_API_KEY")
    add("mistral", "MISTRAL_API_KEYS", "MISTRAL_API_KEY")
    keys["cloudflare_account"] = found.get("CLOUDFLARE_ACCOUNT_ID", "")
    return keys

KEYS = load_keys()

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet, studio vibe
        sys.stderr.write("[studio] %s\n" % (fmt % args))

    # ── helpers ───────────────────────────────────────────────
    def _origin(self):
        o = self.headers.get("Origin") or ""
        return o if (o.startswith("http://127.0.0.1") or o.startswith("http://localhost")) else "*"

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    # ── routes ───────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/keys":
            return self.send_json({
                "cloud": True,
                "groq": len(KEYS["groq"]),
                "gemini": len(KEYS["gemini"]),
                "openrouter": len(KEYS["openrouter"]),
                "mistral": len(KEYS["mistral"]),
            })
        if path == "/api/sfx":
            exts = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
            try:
                sounds = sorted(f for f in os.listdir(SFX_DIR) if f.lower().endswith(exts))
            except OSError:
                sounds = []
            return self.send_json({"sounds": sounds})
        if path.startswith("/api/yt/progress/"):
            job = YT_JOBS.get(path.rsplit("/", 1)[-1])
            if not job:
                return self.send_json({"error": "unknown pull job"}, 404)
            with YT_LOCK:
                return self.send_json(dict(job))
        # static files
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(ROOT) or not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        size = os.path.getsize(full)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.end_headers()
        # pulled youtube files can be hundreds of MB — never buffer them whole
        with open(full, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ── AI proxies ────────────────────────────────────────────
    def do_POST(self):
        path = self.path.split("?")[0]
        body = self.read_body()
        try:
            if path == "/api/transcribe":
                return self.proxy_transcribe(body)
            if path == "/api/highlights":
                return self.proxy_highlights(body)
            if path == "/api/yt":
                return self.yt_start(body)
            self.send_json({"error": "unknown endpoint"}, 404)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            self.send_json({"error": f"upstream {e.code}: {detail}"}, 502)
        except Exception as e:  # noqa: BLE001
            self.send_json({"error": str(e)}, 500)

    # ── youtube link pull ────────────────────────────────────────
    def yt_start(self, body):
        if yt_dlp is None:
            return self.send_json(
                {"error": "yt-dlp isnt installed — run: py -m pip install yt-dlp"}, 503)
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return self.send_json({"error": "bad json"}, 400)

        url = (data.get("url") or "").strip()
        kind = str(data.get("kind") or "video").strip().lower()
        if kind not in ("video", "audio"):
            kind = "video"
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        yt_hosts = ("youtube.com", "youtu.be", "youtube-nocookie.com")
        on_yt = any(host == h or host.endswith("." + h) for h in yt_hosts)
        if parts.scheme not in ("http", "https") or not on_yt:
            return self.send_json({"error": "thats not a youtube link"}, 400)

        jid = hashlib.sha1((url + "|" + kind).encode("utf-8")).hexdigest()[:16]
        with YT_LOCK:
            old = YT_JOBS.get(jid)
            if old:
                if old["status"] in ("queued", "meta", "downloading", "merging"):
                    return self.send_json({"job": jid})   # already pulling it
                if old["status"] == "done" and old.get("file") and os.path.isfile(
                    os.path.join(ROOT, old["file"].lstrip("/"))
                ):
                    return self.send_json({"job": jid})   # cached — instant, no re-download
                # error, or the cached file got purged → run it again below
            job = {"id": jid, "status": "queued", "pct": 0, "url": url, "kind": kind}
            YT_JOBS[jid] = job
            if len(YT_JOBS) > 12:                          # dont leak jobs forever
                for k in list(YT_JOBS)[: len(YT_JOBS) - 12]:
                    if YT_JOBS[k]["status"] not in ("meta", "downloading", "merging"):
                        YT_JOBS.pop(k, None)
        threading.Thread(target=yt_worker, args=(job, url, kind), daemon=True).start()
        return self.send_json({"job": jid}, 202)

    def proxy_transcribe(self, audio):
        keys = KEYS["groq"]
        if not keys:
            return self.send_json({"error": "no GROQ_API_KEY found in .env files"}, 400)
        boundary = "mizstudio7351920"
        model = "whisper-large-v3-turbo"
        parts = []
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nsegment\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nword\r\n".encode())
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n".encode()
        )
        parts.append(audio)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        payload = b"".join(parts)

        last_err = None
        for key in keys:
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": BROWSER_UA,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=600) as res:
                    return self.send_json(json.loads(res.read().decode("utf-8")))
            except urllib.error.HTTPError as e:
                last_err = f"groq {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
                if e.code in (401, 403):
                    continue  # dead key → next
                break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
        return self.send_json({"error": last_err or "transcription failed"}, 502)

    def proxy_highlights(self, body):
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return self.send_json({"error": "bad json"}, 400)

        prompt = (
            "You are a viral clip scout for a gaming YouTuber. Below are timestamped "
            "transcript segments of a video. Pick the best standalone moments as clips.\n"
            f"Video type: {data.get('style', 'gaming')}\n"
            f"Number of clips: {data.get('maxClips', 4)}\n"
            f"Clip length: {data.get('minLen', 15)}-{data.get('maxLen', 60)} seconds.\n"
            "Rules: each clip must be genuinely funny/scary/hype ON ITS OWN (no context needed), "
            "start at a segment boundary, prefer moments with reactions, jumpscares, fails, "
            "comebacks or big emotions. Score 1-10 on viral potential.\n"
            'Reply with ONLY a JSON array like: '
            '[{"start":12.5,"end":47.2,"title":"3-6 word punchy title","hook":"why it slaps in 8 words","score":9}]\n\n'
            "TRANSCRIPT SEGMENTS:\n" + data.get("transcript", "")[:120000]
        )

        attempts = []
        # Gemini first — same model chain the Discord bots use (proven working
        # with these exact keys), then Groq text models, then the free pools.
        for key in KEYS["gemini"]:
            for model in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest"):
                attempts.append((
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    {"Authorization": f"Bearer {key}"},
                    {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
                    f"gemini/{model}",
                ))
        for key in KEYS["groq"]:
            for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
                attempts.append((
                    "https://api.groq.com/openai/v1/chat/completions",
                    {"Authorization": f"Bearer {key}"},
                    {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
                    f"groq/{model}",
                ))
        for key in KEYS["openrouter"]:
            attempts.append((
                "https://openrouter.ai/api/v1/chat/completions",
                {"Authorization": f"Bearer {key}"},
                {"model": "meta-llama/llama-3.3-70b-instruct", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
                "openrouter/llama-3.3-70b",
            ))
        for key in KEYS["mistral"]:
            attempts.append((
                "https://api.mistral.ai/v1/chat/completions",
                {"Authorization": f"Bearer {key}"},
                {"model": "mistral-small-latest", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
                "mistral/small",
            ))

        last_err = "no AI keys found"
        for url, headers, payload, label in attempts:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), method="POST",
                headers={**headers, "Content-Type": "application/json", "User-Agent": BROWSER_UA},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as res:
                    out = json.loads(res.read().decode("utf-8"))
                text = out["choices"][0]["message"]["content"]
                m = re.search(r"\[[\s\S]*\]", text)
                clips = json.loads(m.group(0)) if m else []
                if clips:
                    return self.send_json({"clips": clips})
                last_err = f"{label}: no parseable clips in reply"
            except urllib.error.HTTPError as e:
                last_err = f"{label}: HTTP {e.code}"
                continue
            except Exception as e:  # noqa: BLE001
                last_err = f"{label}: {e}"
        return self.send_json({"error": last_err}, 502)


if __name__ == "__main__":
    os.chdir(ROOT)
    n_groq = len(KEYS["groq"])
    n_gem = len(KEYS["gemini"])
    print("=" * 54)
    print("  MIZVILLAN STUDIO — local ai server")
    print(f"  keys loaded: groq x{n_groq} · gemini x{n_gem} (never printed)")
    print(f"  open http://localhost:{PORT} in your browser")
    print("  ctrl+c to stop")
    print("=" * 54)
    if not n_groq and not n_gem:
        print("  ! no AI keys found — check the .env paths in studio-server.py")
    ThreadingHTTPServer.allow_reuse_address = True
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[studio] later gng")

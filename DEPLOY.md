# Deploy MIZVILLAN STUDIO

## ✅ Live right now (GitHub Pages — already set up)

**https://mizvillan.github.io/mizvillan-studio/**

The site is 100% static — no server, no account needed to host it. The browser
talks to Groq (captions/transcript) and Gemini (clip hunter) **directly** using
keys you paste into the app.

### How visitors use it

1. Open the site → click **keys** in the top bar.
2. Paste a **Groq** key (captions + transcript) and/or a **Gemini** key (clip hunter).
3. Keys are saved in that browser's localStorage only — never sent to any server
   of ours, only to Groq/Google themselves.

Where to get them (both free):

- Groq → https://console.groq.com/keys
- Gemini → https://aistudio.google.com/apikey

### Updating the site

```bash
git add index.html
git commit -m "update"
git push
```

GitHub Pages rebuilds automatically (~1 min). Settings live at
https://github.com/mizvillan/mizvillan-studio/settings/pages (source: `master`, root).

## 🔗 YouTube link → short (runs on the local server only)

Paste a youtube video/shorts link in the **youtube link → short** box on the
import tab. The studio pulls the video, flips to **clip hunter**, transcribes it
and drops the viral moments on the timeline — export one vertical and you've got
your short. Long recordings of your own are still dropped in as files.

- Needs `yt-dlp` + a static ffmpeg: `py -m pip install yt-dlp imageio-ffmpeg`
- Pulled videos land in `_yt/` (gitignored, last 6 kept, ~30 min / 1080p max)
- Anything over ~13 min is transcribed in chunks — groq caps uploads at 25MB
- **The live GitHub Pages site can't do this**: a static page can't reach
  youtube (CORS), so link pull only works when the studio runs from
  `py studio-server.py` → http://localhost:8787. On the live site the box tells
  you this instead of failing silently. Everything else (file import, captions,
  clip hunt, export) works on both.

## Local dev with the python server (optional)

Gives you a key proxy so you don't have to paste keys — reads keys from
`../Kyronator/.env` and `../Mizvillan's bot/.env`:

```bash
py studio-server.py
# → open http://localhost:8787
```

The app auto-discovers it: `findServer()` probes `/api/keys` on the current
origin, then `http://localhost:8787` / `http://127.0.0.1:8787`, and the badge in
the top bar shows which brain is active.

## Vercel (optional, if you ever want a server-side proxy)

The `api/` folder + `vercel.json` hold an unfinished edge-proxy attempt
(`/api/whisper`, `/api/keys`, `/api/gemini`) so keys could live in env vars
instead of the browser. Not required — the GitHub Pages + paste-keys path is
what's deployed. If you build it out: `vercel login`, `vercel --prod`, then set
`GROQ_API_KEY` / `GEMINI_API_KEY` in the Vercel dashboard.

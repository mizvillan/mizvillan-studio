# Deploy MIZVILLAN STUDIO to Vercel (free)

## 1. Set up

1. Sign up at [vercel.com](https://vercel.com/signup) (free tier)
2. Install Vercel CLI:
   ```bash
   npm install -g vercel
   ```
3. Login:
   ```bash
   vercel login
   ```
4. In this folder, link/create the project:
   ```bash
   vercel
   ```
   Pick "create new project", name it `mizvillan-studio`.

## 2. Add your API keys (env vars)

In your Vercel dashboard → Project Settings → Environment Variables, add:

- `GROQ_API_KEY` → your Groq key (from Kyronator .env)
- `GEMINI_API_KEY` → your Gemini key

## 3. Deploy

```bash
vercel --prod
```

Your studio goes live at `https://mizvillan-studio.vercel.app` (or a custom domain you add).

## Done.

The `/api/whisper` and `/api/keys` edge functions proxy requests
to Groq using your key — it stays in Vercel env vars, never in the frontend JS.

## Local dev (alternative)

If you just want to run locally without Vercel:

```bash
python studio-server.py
# → open http://localhost:8787
```
export const config = { runtime: 'edge' };

export default async function handler(req) {
  const GEMINI_KEY = process.env.GEMINI_API_KEY;
  if (!GEMINI_KEY) return new Response(JSON.stringify({ error: 'no gemini key' }), { status: 500 });

  const body = await req.json();
  const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${GEMINI_KEY}`;

  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const data = await resp.json();
  return new Response(JSON.stringify(data), { status: resp.status });
}
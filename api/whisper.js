export const config = { runtime: 'edge' };

export default async function handler(req) {
  const GROQ_KEY = process.env.GROQ_API_KEY;
  if (!GROQ_KEY) return new Response(JSON.stringify({ error: 'no groq key' }), { status: 500 });

  const url = new URL(req.url);
  url.host = 'api.groq.com';
  url.pathname = '/openai/v1/audio/transcriptions';

  const modifiedReq = new Request(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${GROQ_KEY}`,
      ...req.headers,
    },
  });
  delete modifiedReq.headers['host'];

  const resp = await fetch(modifiedReq);
  const data = await resp.json();
  return new Response(JSON.stringify(data), { status: resp.status });
}
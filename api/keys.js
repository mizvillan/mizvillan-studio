export const config = { runtime: 'edge' };

export default async function handler() {
  const groq = process.env.GROQ_API_KEY ? 1 : 0;
  const gemini = process.env.GEMINI_API_KEY ? 1 : 0;
  return new Response(JSON.stringify({ groq, gemini }), { status: 200 });
}
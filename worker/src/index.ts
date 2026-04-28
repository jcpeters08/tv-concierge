/**
 * tv-concierge-auth
 *
 * Magic-link login + encrypted PAT vault for the TV Concierge web app.
 *
 * Flow:
 *   1. App POSTs to /auth/request with {email}. Worker validates against the
 *      allowlist, mints a one-time magic-link token, stores it in KV, and
 *      emails a link via Resend.
 *   2. User clicks the link. /auth/verify consumes the token, mints a session,
 *      and redirects back to the app with the session id in the URL fragment
 *      (`#sid=...`) — fragments are not sent to the server, so this never
 *      lands in any access log.
 *   3. App stores the session id in localStorage and uses it as a Bearer
 *      token on subsequent requests.
 *   4. App calls GET /pat to fetch the encrypted-at-rest PAT (AES-GCM with a
 *      Worker secret key). On first sign-in there is no PAT yet, so the app
 *      prompts for one and PUTs it.
 */

interface Env {
  KV: KVNamespace;
  ALLOWED_ORIGIN: string;
  APP_URL: string;
  ALLOWED_EMAILS: string;       // comma-separated
  RESEND_API_KEY: string;       // secret
  PAT_ENC_KEY: string;          // secret, base64-encoded 32 bytes
}

const MAGIC_TTL_SECONDS   = 15 * 60;
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;

// ---- Entry point ------------------------------------------------------------

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return cors(env, new Response(null, { status: 204 }));

    try {
      if (url.pathname === "/auth/request" && request.method === "POST") return cors(env, await handleAuthRequest(request, env));
      if (url.pathname === "/auth/verify"  && request.method === "GET")  return cors(env, await handleAuthVerify(request, env));
      if (url.pathname === "/auth/logout"  && request.method === "POST") return cors(env, await handleAuthLogout(request, env));
      if (url.pathname === "/auth/me"      && request.method === "GET")  return cors(env, await handleAuthMe(request, env));
      if (url.pathname === "/pat"          && request.method === "GET")  return cors(env, await handleGetPat(request, env));
      if (url.pathname === "/pat"          && request.method === "PUT")  return cors(env, await handlePutPat(request, env));
      return cors(env, json({ error: "not found" }, 404));
    } catch (err) {
      console.error(err);
      return cors(env, json({ error: "internal error" }, 500));
    }
  },
};

// ---- Routes ----------------------------------------------------------------

async function handleAuthRequest(request: Request, env: Env): Promise<Response> {
  const body = await safeJson(request);
  const email = String(body?.email ?? "").trim().toLowerCase();
  if (!email || !email.includes("@")) return json({ error: "invalid email" }, 400);

  const allowed = env.ALLOWED_EMAILS.split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  // Always return ok to avoid leaking which emails are allowed; only actually
  // send when the email is on the allowlist.
  if (!allowed.includes(email)) return json({ ok: true });

  const token = randomToken(32);
  await env.KV.put(`magic:${token}`, JSON.stringify({ email }), { expirationTtl: MAGIC_TTL_SECONDS });

  const workerOrigin = new URL(request.url).origin;
  const link = `${workerOrigin}/auth/verify?token=${encodeURIComponent(token)}`;
  await sendMagicLinkEmail(env, email, link);

  return json({ ok: true });
}

async function handleAuthVerify(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token") ?? "";
  if (!token) return html("Missing token.", 400);

  const raw = await env.KV.get(`magic:${token}`);
  if (!raw) return html("This sign-in link has expired or already been used. Request a new one.", 400);
  await env.KV.delete(`magic:${token}`);  // single-use

  const { email } = JSON.parse(raw) as { email: string };

  const sid = randomToken(32);
  await env.KV.put(`session:${sid}`, JSON.stringify({ email }), { expirationTtl: SESSION_TTL_SECONDS });

  const redirect = new URL(env.APP_URL);
  redirect.hash = `sid=${encodeURIComponent(sid)}`;
  return Response.redirect(redirect.toString(), 302);
}

async function handleAuthLogout(request: Request, env: Env): Promise<Response> {
  const sid = bearer(request);
  if (sid) await env.KV.delete(`session:${sid}`);
  return json({ ok: true });
}

async function handleAuthMe(request: Request, env: Env): Promise<Response> {
  const session = await loadSession(request, env);
  if (!session) return json({ error: "unauthorized" }, 401);
  return json({ email: session.email });
}

async function handleGetPat(request: Request, env: Env): Promise<Response> {
  const session = await loadSession(request, env);
  if (!session) return json({ error: "unauthorized" }, 401);

  const stored = await env.KV.get(`pat:${session.email}`);
  if (!stored) return json({ pat: null });

  const pat = await decryptString(env.PAT_ENC_KEY, stored);
  return json({ pat });
}

async function handlePutPat(request: Request, env: Env): Promise<Response> {
  const session = await loadSession(request, env);
  if (!session) return json({ error: "unauthorized" }, 401);

  const body = await safeJson(request);
  const pat = String(body?.pat ?? "").trim();
  if (!pat) return json({ error: "missing pat" }, 400);

  const ciphertext = await encryptString(env.PAT_ENC_KEY, pat);
  await env.KV.put(`pat:${session.email}`, ciphertext);
  return json({ ok: true });
}

// ---- Auth helpers ----------------------------------------------------------

async function loadSession(request: Request, env: Env): Promise<{ email: string } | null> {
  const sid = bearer(request);
  if (!sid) return null;
  const raw = await env.KV.get(`session:${sid}`);
  if (!raw) return null;
  return JSON.parse(raw) as { email: string };
}

function bearer(request: Request): string | null {
  const h = request.headers.get("Authorization") ?? "";
  const m = h.match(/^Bearer\s+(.+)$/i);
  return m ? m[1].trim() : null;
}

// ---- Email -----------------------------------------------------------------

async function sendMagicLinkEmail(env: Env, to: string, link: string): Promise<void> {
  const html = `
    <p>Click this link to sign in to TV Concierge. It's valid for 15 minutes and works only once.</p>
    <p><a href="${escapeHtml(link)}">Sign in to TV Concierge</a></p>
    <p style="color:#888;font-size:12px">If you didn't request this, you can ignore the email.</p>
  `;
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "TV Concierge <onboarding@resend.dev>",
      to: [to],
      subject: "Your sign-in link for TV Concierge",
      html,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Resend error ${res.status}: ${text}`);
  }
}

// ---- Crypto (AES-GCM with a Worker secret key) -----------------------------

async function importKey(b64: string): Promise<CryptoKey> {
  const raw = base64ToBytes(b64);
  if (raw.byteLength !== 32) throw new Error("PAT_ENC_KEY must be 32 bytes (base64-encoded)");
  return crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptString(keyB64: string, plain: string): Promise<string> {
  const key = await importKey(keyB64);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(plain));
  // Output: base64(IV || ciphertext+tag)
  const out = new Uint8Array(iv.byteLength + ct.byteLength);
  out.set(iv, 0);
  out.set(new Uint8Array(ct), iv.byteLength);
  return bytesToBase64(out);
}

async function decryptString(keyB64: string, blobB64: string): Promise<string> {
  const key = await importKey(keyB64);
  const blob = base64ToBytes(blobB64);
  const iv = blob.slice(0, 12);
  const ct = blob.slice(12);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct);
  return new TextDecoder().decode(pt);
}

// ---- HTTP / encoding utilities ---------------------------------------------

function cors(env: Env, res: Response): Response {
  const headers = new Headers(res.headers);
  headers.set("Access-Control-Allow-Origin", env.ALLOWED_ORIGIN);
  headers.set("Vary", "Origin");
  headers.set("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Authorization, Content-Type");
  headers.set("Access-Control-Max-Age", "86400");
  return new Response(res.body, { status: res.status, headers });
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function html(message: string, status = 200): Response {
  return new Response(`<!doctype html><meta charset=utf-8><body style="font-family:system-ui;padding:32px;max-width:560px;margin:auto"><p>${escapeHtml(message)}</p>`, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

async function safeJson(request: Request): Promise<Record<string, unknown> | null> {
  try { return await request.json() as Record<string, unknown>; } catch { return null; }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
}

function randomToken(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return bytesToBase64Url(buf);
}

function bytesToBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.byteLength; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

function bytesToBase64Url(bytes: Uint8Array): string {
  return bytesToBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64ToBytes(b64: string): Uint8Array {
  const norm = b64.replace(/-/g, "+").replace(/_/g, "/");
  const pad = norm.length % 4 === 0 ? "" : "=".repeat(4 - (norm.length % 4));
  const bin = atob(norm + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

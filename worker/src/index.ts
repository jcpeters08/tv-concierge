/**
 * tv-concierge-auth
 *
 * Email-code login + encrypted PAT vault for the TV Concierge web app.
 *
 * Flow:
 *   1. App POSTs to /auth/request with {email}. Worker validates against the
 *      allowlist, generates a 6-digit code, stores it in KV under code:<email>
 *      (15-min TTL, 5-attempt cap), and emails the code via Resend.
 *   2. User reads the code and POSTs it to /auth/verify-code with their
 *      email. Worker validates, mints a session, and returns {sid} as JSON.
 *      The whole flow stays in the same browser tab — no clickable link, no
 *      cross-app redirect — which is what the iOS PWA needs (PWAs have an
 *      isolated localStorage scope from Safari, so clickable links land in
 *      the wrong context).
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

const CODE_TTL_SECONDS    = 15 * 60;
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;
const MAX_CODE_ATTEMPTS   = 5;

// ---- Entry point ------------------------------------------------------------

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return cors(env, new Response(null, { status: 204 }));

    try {
      if (url.pathname === "/auth/request"     && request.method === "POST") return cors(env, await handleAuthRequest(request, env));
      if (url.pathname === "/auth/verify-code" && request.method === "POST") return cors(env, await handleAuthVerifyCode(request, env));
      if (url.pathname === "/auth/logout"      && request.method === "POST") return cors(env, await handleAuthLogout(request, env));
      if (url.pathname === "/auth/me"          && request.method === "GET")  return cors(env, await handleAuthMe(request, env));
      if (url.pathname === "/pat"              && request.method === "GET")  return cors(env, await handleGetPat(request, env));
      if (url.pathname === "/pat"              && request.method === "PUT")  return cors(env, await handlePutPat(request, env));
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

  const code = generateCode();
  await env.KV.put(`code:${email}`, JSON.stringify({ code, attempts: 0 }), { expirationTtl: CODE_TTL_SECONDS });
  await sendCodeEmail(env, email, code);

  return json({ ok: true });
}

async function handleAuthVerifyCode(request: Request, env: Env): Promise<Response> {
  const body = await safeJson(request);
  const email = String(body?.email ?? "").trim().toLowerCase();
  const code = String(body?.code ?? "").trim();
  if (!email || !code) return json({ error: "missing email or code" }, 400);

  const key = `code:${email}`;
  const raw = await env.KV.get(key);
  if (!raw) return json({ error: "code expired or not requested" }, 400);
  const data = JSON.parse(raw) as { code: string; attempts: number };

  if (data.attempts >= MAX_CODE_ATTEMPTS) {
    await env.KV.delete(key);
    return json({ error: "too many attempts" }, 400);
  }

  if (data.code !== code) {
    data.attempts += 1;
    await env.KV.put(key, JSON.stringify(data), { expirationTtl: CODE_TTL_SECONDS });
    return json({ error: "invalid code" }, 400);
  }

  await env.KV.delete(key);

  const sid = randomToken(32);
  await env.KV.put(`session:${sid}`, JSON.stringify({ email }), { expirationTtl: SESSION_TTL_SECONDS });

  return json({ sid });
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

async function sendCodeEmail(env: Env, to: string, code: string): Promise<void> {
  const html = `
    <p>Your sign-in code for TV Concierge:</p>
    <p style="font-size:28px;font-weight:bold;letter-spacing:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;margin:16px 0">${escapeHtml(code)}</p>
    <p>Enter this code in the app. It's valid for 15 minutes.</p>
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
      subject: "Your sign-in code for TV Concierge",
      html,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Resend error ${res.status}: ${text}`);
  }
}

function generateCode(): string {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return String(buf[0] % 1_000_000).padStart(6, "0");
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

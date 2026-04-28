# tv-concierge-auth

Cloudflare Worker that handles magic-link sign-in and vaults the GitHub PAT for the TV Concierge web app, so any device that signs in inherits the same token without re-running setup.

## Architecture

- `POST /auth/request {email}` — emails a magic link via Resend (allowlist-protected).
- `GET  /auth/verify?token=...` — consumes the magic-link token, mints a session, redirects to the app with the session id in the URL fragment (`#sid=...`).
- `GET  /auth/me` — returns the signed-in email or 401.
- `POST /auth/logout` — invalidates the current session.
- `GET  /pat` — returns the decrypted PAT (or `null` if not yet stored).
- `PUT  /pat {pat}` — encrypts and stores the PAT (AES-GCM with a Worker secret key).

Sessions and the encrypted PAT live in a single Cloudflare KV namespace:

| Key | Value | TTL |
|---|---|---|
| `magic:<token>` | `{email}` | 15 min |
| `session:<sid>` | `{email}` | 30 days |
| `pat:<email>` | base64(IV ‖ AES-GCM ciphertext) | none |

The frontend uses the session id as a Bearer token. We do **not** use cookies — that avoids cross-origin cookie complications between GitHub Pages and `*.workers.dev`.

## One-time deploy

Run from this directory.

```bash
npm install

# 1. Authenticate to Cloudflare (browser OAuth).
npx wrangler login

# 2. Create the KV namespaces and paste the returned IDs into wrangler.toml.
npx wrangler kv namespace create AUTH_KV
npx wrangler kv namespace create AUTH_KV --preview

# 3. Set secrets (you'll be prompted for each value).
npx wrangler secret put RESEND_API_KEY     # paste your Resend API key
npx wrangler secret put PAT_ENC_KEY        # paste a base64'd 32-byte key

# Generate PAT_ENC_KEY locally:
openssl rand -base64 32

# 4. Deploy.
npx wrangler deploy
```

Wrangler will print the worker URL (e.g. `https://tv-concierge-auth.<subdomain>.workers.dev`). Update the frontend's `WORKER_URL` constant with that value.

## Local dev

```bash
npx wrangler dev
```

Wrangler dev uses the `preview_id` KV namespace and prompts for any missing secrets. Hit `http://localhost:8787/auth/me` with a Bearer token to smoke-test.

## Rotating the encryption key

If `PAT_ENC_KEY` rotates, all existing `pat:<email>` ciphertexts become unreadable. Sign in fresh on any device, re-enter the PAT, and the new key encrypts it.

## Updating the allowlist

Edit `ALLOWED_EMAILS` in `wrangler.toml` (comma-separated) and redeploy.

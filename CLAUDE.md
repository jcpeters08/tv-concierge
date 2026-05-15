# CLAUDE.md — TV Concierge

If you're a new Claude session opening this repo, read this first.

## What this is

Personal evening TV/movie briefing for Jonathan. Static web app on GitHub Pages backed by JSON files in this repo. The Obsidian vault is source of truth; a daily Cowork Scheduled Task syncs in both directions.

Live: https://jcpeters08.github.io/tv-concierge/

## Architecture (4 layers)

```
Vault (source of truth)         Repo (data + viewer)         Live web app
────────────────────────        ──────────────────────       ──────────────
Watched.md                ←─→   data/watched.json       ←─→  index.html
Watchlist.md              ←─→   data/watchlist.json
Not-Interested.md         ←─→   data/not-interested.json
Overview.md               ──→   data/taste-profile.json
                                data/recommendations.json
                                data/pending.json        ←──  (web app appends)
```

Same shape as pt-tracker. Web app appends to `pending.json` via the GitHub Contents API (PAT in localStorage initially; auth Worker added later — see pt-tracker for pattern). Daily Cowork task drains pending → vault MD, re-derives JSON snapshots, refreshes recommendations on Sundays.

## Critical conventions — DON'T BREAK

1. **Vault MD is source of truth.** `scripts/sync.py` re-derives `data/*.json` from vault every morning. Don't hand-edit JSON.
2. **Writes are append-only to `pending.json`.** The web app NEVER writes other data files directly.
3. **Sundays refresh recommendations.** The sync also runs web search + taste-match on Sundays to update `recommendations.json`.

## Glossary

- **Watched** — rated entries (1–10 with mini-notes), parsed from vault `Watched.md`
- **Watchlist** — pinned shows/movies queued for later, vault `Watchlist.md`
- **Not Interested** — explicit rejections so they don't resurface in recommendations
- **Taste profile** — distilled from vault Overview.md; drives the Sunday recommendation refresh

## Operational pointers

- **Build brief**: `~/Documents/Jonathan's Vault/🎯 Projects/🎬 TV and Movie Concierge/Web-App-Build-Brief.md`
- **Scheduled task**: `~/.claude/scheduled-tasks/tv-concierge-daily-sync/SKILL.md` (daily 8am CT)
- **Vault path**: `~/Documents/Jonathan's Vault/🎯 Projects/🎬 TV and Movie Concierge/`
- **Auth Worker** (if deployed): Cloudflare Worker pattern — see pt-tracker's `worker/` for reference
- **Vault MD edits**: via Cowork (vault filesystem is sandboxed from this session)

## Status notes

This repo's bootstrap commit seeded the data layer only. The web app (`index.html`), sync script (`scripts/sync.py`), parsers, and deploy workflow may or may not be built — check current state with `ls scripts/ && [ -f index.html ] && echo "app exists"`. The README has the build-out checklist.

## Where to look for more

- `README.md` — bootstrap layout, build-out checklist, vault build brief pointer
- Vault `Web-App-Build-Brief.md` — the full spec for Claude Code
- `git log --oneline -30` — what's been built
- Related: pt-tracker (the cousin project — same architecture, more mature; mine it for patterns)

## CLAUDE.md update workflow

On material changes, the active session proactively offers an update. Say "update CLAUDE.md" anytime for explicit invocation.

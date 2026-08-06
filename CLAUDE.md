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
4. **JustWatch is ground truth for streaming availability — MANDATORY before claiming "streaming on X".** Past incidents: on 5/15 and again on 5/17, recommendations.json claimed Demon Slayer: Infinity Castle was on Crunchyroll since April 9 — sourced from a single speculative news article. JustWatch returned no offer; the film was theatrical-only. The same 5/17 pass mis-stated Jujutsu Kaisen S3 as "currently airing weekly" (S3 Part 1 had ended Mar 27) and Re:ZERO S3 as "currently airing weekly" (it ended Mar 2025; S4 is the current run). Rule below.

## Recommendation verification protocol (applies to every Sunday refresh and ad-hoc rec edits)

For each entry in `recommendations.json`:

- **"Streaming on X right now" claims** → must be verified against `justwatch.com/us/...` for that title in the same session. Industry news articles can be aspirational/speculative. Press releases can be premature. **JustWatch reflects current platform reality.** If JustWatch and news disagree, JustWatch wins. If JustWatch shows no offer, the title is NOT currently streaming — list it under `upcoming_alerts` with "streaming date TBD" instead.
- **Forward drops (announced future arrivals)** → require ≥2 authoritative sources (Variety / Deadline / THR / official platform PR like `apple.com/tv-pr/` or `aboutamazon.com`). One aggregator article is not enough.
- **Anime "currently airing"** → confirm against this week's actual release page on Crunchyroll, not a season-overview article. Distinguish season-finished-airing (binge-ready) from currently-airing-weekly from announced-but-not-started.
- **Don't trust prior `recommendations.json`** — re-verify each session. A claim that survived the last refresh may have been wrong then too.
- **Episode counts and "Ep N" claims** → cross-check against the latest episode listed on JustWatch or the show's official platform page. Don't assume "Ep N+1 drops next week" without confirming the show is still on a weekly cadence.
- **When a search returns contradictory signals** (e.g., "X is on Y" alongside "X is not on Y yet"), surface the contradiction and resolve it before citing. Don't pick the confident-sounding result and ignore the others.
- **Bench entries are not exempt** — `bench[]` catalog picks get the same JustWatch re-check every refresh; titles rotate off platforms constantly.

If a recommendation can't be verified to this standard, omit it or move it to `upcoming_alerts` with explicit "unverified" or "TBD" labeling. The cost of a missing pick is low; the cost of recommending something Jon can't actually watch is trust.

## Recommendation content rules (added Aug 5, 2026)

- **Platforms (5):** Netflix, Apple TV+, Amazon Prime Video, Crunchyroll, Disney+ — Disney+ includes the full Hulu library as an in-app hub (2026 merger; standalone Hulu app phasing out). Label Hulu-hub titles `Disney+ (Hulu hub)`; the app's "Disney+ (Hulu)" filter chip matches anything containing "disney" or "hulu".
- **No "skip" recs.** `match` is strong/try only. If a title isn't worth Jon's time, omit it; Not-Interested titles never resurface, even as reminders.
- **Comedy guarantee:** active recs (new_this_week + bench) always include at least one comedy movie AND one comedy series.
- **Bench / full filter coverage:** `bench[]` holds verified catalog picks (marked `"bench": true`) so every filter chip — each genre, format (series/anime/movie), and length (30-min/1-hour) — has ≥4 picks across new_this_week + bench. The app hides bench entries on the default page and shows them only when a filter is active. Bench persists across refreshes: re-verify availability, drop watched/hidden titles, top up thin chips. Runtime strings must include per-episode minutes so the length filter can classify.

## Glossary

- **Watched** — rated entries (1–10 with mini-notes), parsed from vault `Watched.md`
- **Watchlist** — pinned shows/movies queued for later, vault `Watchlist.md`
- **Not Interested** — explicit rejections so they don't resurface in recommendations
- **Taste profile** — distilled from vault Overview.md; drives the Sunday recommendation refresh

## Operational pointers

- **Build brief**: `~/Documents/Jonathan's Vault/🎯 Projects/🎬 TV and Movie Concierge/Web-App-Build-Brief.md`
- **Scheduled task**: `~/.claude/scheduled-tasks/tv-concierge-daily-sync/SKILL.md` (daily 8am CT)
- **Cowork Git bridge**: `scripts/cowork_git_bridge.py` prepares a disposable clone under `/tmp` for scheduled-task Git operations. Do not run `git pull/add/commit/push` from the Cowork-mounted repo; the sandbox mount can strand `.git/*.lock` files.
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

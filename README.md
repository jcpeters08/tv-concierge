# 🎬 TV Concierge

Personal evening TV/movie briefing for Jonathan. Static web app on GitHub Pages backed by JSON files in this repo. The Obsidian vault remains source of truth; a daily Cowork Scheduled Task syncs in both directions.

**Live app:** https://jcpeters08.github.io/tv-concierge/ (after Pages deploys)

## What's here so far

This is the **bootstrap commit** — only the data layer is populated. Claude Code will build the web app (`index.html`), the sync script (`scripts/sync.py`), the install scripts, and the deploy workflow.

```
tv-concierge/
├── data/
│   ├── recommendations.json    ✅ seeded with Apr 27 – May 3, 2026 picks
│   ├── taste-profile.json      ✅ seeded from vault Overview.md
│   ├── watched.json            ✅ seeded from vault Watched.md (29 rated entries)
│   ├── watchlist.json          ✅ empty (created on first 📌 click)
│   ├── not-interested.json     ✅ empty (created on first 🚫 click)
│   └── pending.json            ✅ empty (web app's append-only inbox)
├── index.html                  ⏳ Claude Code TODO
├── scripts/
│   ├── sync.py                 ⏳ Claude Code TODO
│   ├── parse_watched.py        ⏳ Claude Code TODO
│   └── install-launchd.sh      ⏳ optional, Claude Code TODO
├── .github/workflows/
│   └── deploy.yml              ⏳ Claude Code TODO
└── README.md                   ✅ this file
```

## Build brief

The full spec for Claude Code lives in the vault at:
`~/Documents/Jonathan's Vault/🎯 Projects/🎬 TV and Movie Concierge/Web-App-Build-Brief.md`

Hand that to Claude Code as the prompt.

## Architecture (one paragraph)

The web app reads `data/*.json` from this repo (same-origin). Writes go ONLY to `data/pending.json` via the GitHub Contents API using a fine-grained PAT stored in browser localStorage. A daily Cowork Scheduled Task at 8am CT runs `scripts/sync.py`, which:
1. Drains `pending.json` into the vault MD files (Watched.md / Watchlist.md / Not-Interested.md)
2. Re-derives `data/*.json` snapshots from the vault MD
3. Resets `pending.json` to `{entries: []}`
4. On Sundays, also refreshes `recommendations.json` via web search + taste-match

The vault stays source of truth. The web app is a viewer + capture surface.

## Setup checklist (in order)

- [x] Bootstrap data files
- [ ] Claude Code: build `index.html`, `scripts/sync.py`, deploy workflow
- [ ] Push to GitHub, enable Pages
- [ ] Create fine-grained PAT scoped to this repo (Contents: Read+Write)
- [ ] Visit the live URL, paste PAT into setup panel
- [ ] Set up the Cowork Scheduled Task (daily 8am CT, prompt body in build brief)
- [ ] Trigger task on demand to verify end-to-end sync

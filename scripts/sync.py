#!/usr/bin/env python3
"""Bidirectional vault <-> repo sync for TV Concierge.

Pure stdlib. Run from anywhere — paths are resolved from env or sensible
defaults. The model: vault MD is source of truth. The web app appends
entries to data/pending.json; this script drains them into vault MD,
then re-derives the JSON snapshots from vault MD, resets pending, and
commits + pushes if anything changed.

Algorithm summary (see Web-App-Build-Brief.md for the full spec):
  1. git pull
  2. Drain data/pending.json into vault MD (Watched / Watchlist /
     Not-Interested), in ts-ascending order. Conflicts (already-rated
     watched, already-pinned, already-hidden) are skipped with a warning;
     duplicate-removal is a no-op if the line is already gone.
  3. Re-derive data/{watched,watchlist,not-interested,taste-profile}.json
     from vault MD. taste-profile.json merges parsed fields over the
     prior snapshot so derived fields (signals_what_lands etc., which
     live in the deep memory file, not vault MD) survive untouched.
  4. Reset pending.json to {entries: []}.
  5. git add data/; commit + push if anything actually changed.
  6. Print a summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import parse_watched as pw  # noqa: E402

DEFAULT_VAULT = Path.home() / "Documents" / "Jonathan's Vault"
DEFAULT_REPO = Path.home() / "Git" / "tv-concierge"

PROJECT_REL = Path("\U0001f3af Projects") / "\U0001f3ac TV and Movie Concierge"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), check=check, capture_output=True, text=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_snapshot_if_changed(path: Path, new_data: dict, synced_at: str, ignore_keys=("synced_at",)) -> bool:
    """Write `new_data` to `path` only if it differs from the existing file
    after stripping `ignore_keys`. Sets new_data[synced_at] = synced_at on
    write; preserves prior synced_at on no-op. Returns True if written.
    """
    prior: dict = {}
    if path.exists():
        try:
            prior = json.loads(path.read_text())
        except json.JSONDecodeError:
            prior = {}

    def strip(d: dict) -> dict:
        return {k: v for k, v in d.items() if k not in ignore_keys}

    if strip(prior) == strip(new_data):
        # No real diff — keep existing synced_at and existing serialization.
        return False

    new_data["synced_at"] = synced_at
    write_json(path, new_data)
    return True


def read_text_or(path: Path, default: str) -> str:
    if path.exists():
        return path.read_text()
    return default


def main() -> int:
    vault_root = Path(os.environ.get("TV_CONCIERGE_VAULT_ROOT", str(DEFAULT_VAULT)))
    repo_root = Path(os.environ.get("TV_CONCIERGE_REPO_ROOT", str(DEFAULT_REPO)))
    project_dir = vault_root / PROJECT_REL

    if not project_dir.exists():
        print(f"ERROR: vault project folder missing: {project_dir}", file=sys.stderr)
        return 2
    if not (repo_root / "data").exists():
        print(f"ERROR: repo data dir missing: {repo_root}/data", file=sys.stderr)
        return 2

    # Step 1: git pull
    try:
        run(["git", "pull", "--ff-only"], cwd=repo_root)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: git pull failed: {e.stderr}", file=sys.stderr)
        return 3

    pending_path = repo_root / "data" / "pending.json"
    pending = read_json(pending_path)
    entries = sorted(pending.get("entries", []), key=lambda e: e.get("ts", ""))

    watched_md = project_dir / "Watched.md"
    watchlist_md = project_dir / "Watchlist.md"
    notint_md = project_dir / "Not-Interested.md"

    watched_text = watched_md.read_text() if watched_md.exists() else ""
    watchlist_text = read_text_or(watchlist_md, pw.empty_titles_doc("watchlist"))
    notint_text = read_text_or(notint_md, pw.empty_titles_doc("not_interested"))

    drained: list[str] = []
    skipped: list[str] = []

    # Step 2: drain pending into vault MD
    for entry in entries:
        kind = entry.get("kind")
        title = (entry.get("title") or "").strip()
        if not title:
            skipped.append(f"(no title) kind={kind}")
            continue
        if kind == "watched":
            if title in pw.watched_titles_in(watched_text):
                skipped.append(f"watched (already rated): {title}")
                continue
            line = pw.format_watched_line(
                title=title,
                rating=entry.get("rating", 0),
                tags=entry.get("tags", "") or "",
                date=entry.get("date") or now_iso()[:10],
            )
            watched_text = pw.append_watched_entry(watched_text, line)
            drained.append(f"watched: {title}")
        elif kind == "pin":
            watchlist_text, changed = pw.append_title(watchlist_text, title)
            if changed:
                drained.append(f"pin: {title}")
            else:
                skipped.append(f"pin (already in watchlist): {title}")
        elif kind == "unpin":
            watchlist_text, changed = pw.remove_title(watchlist_text, title)
            if changed:
                drained.append(f"unpin: {title}")
            else:
                skipped.append(f"unpin (not in watchlist): {title}")
        elif kind == "hide":
            notint_text, changed = pw.append_title(notint_text, title)
            if changed:
                drained.append(f"hide: {title}")
            else:
                skipped.append(f"hide (already hidden): {title}")
        elif kind == "unhide":
            notint_text, changed = pw.remove_title(notint_text, title)
            if changed:
                drained.append(f"unhide: {title}")
            else:
                skipped.append(f"unhide (not hidden): {title}")
        else:
            skipped.append(f"unknown kind '{kind}': {title}")

    # Write back vault MD only if changed
    if watched_md.exists():
        if watched_text != watched_md.read_text():
            watched_md.write_text(watched_text)
    elif watched_text:
        watched_md.write_text(watched_text)

    if watchlist_text != read_text_or(watchlist_md, pw.empty_titles_doc("watchlist")):
        watchlist_md.write_text(watchlist_text)
    if notint_text != read_text_or(notint_md, pw.empty_titles_doc("not_interested")):
        notint_md.write_text(notint_text)

    # Step 3: re-derive JSON snapshots from vault MD
    synced_at = now_iso()

    parsed_watched = pw.parse_watched_md(watched_text)
    new_watched_json = {
        "source": "Parsed from vault Watched.md. Re-synced by the daily scheduled task.",
        "format_note": "entries[] preserves the vault format: Title – Rating⭐ – \"tags\" (date). Tags are kept as a comma-separated string for round-trip fidelity, plus a parsed tags_array for convenience.",
        "entries": parsed_watched["entries"],
        "known_favorites": parsed_watched["known_favorites"],
        "known_dislikes": parsed_watched["known_dislikes"],
    }

    new_watchlist_json = {
        "source": "Parsed from vault Watchlist.md (created on first 📌 Interested click). Re-synced by the daily scheduled task.",
        "titles": pw.parse_titles_md(watchlist_text),
    }
    new_notint_json = {
        "source": "Parsed from vault Not-Interested.md (created on first 🚫 Not Interested click). Re-synced by the daily scheduled task.",
        "titles": pw.parse_titles_md(notint_text),
    }

    overview_md = project_dir / "Overview.md"
    parsed_taste = pw.parse_overview_md(overview_md.read_text()) if overview_md.exists() else {}
    taste_path = repo_root / "data" / "taste-profile.json"
    prior_taste = read_json(taste_path) if taste_path.exists() else {}
    new_taste = {k: v for k, v in prior_taste.items() if k != "synced_at"}
    new_taste.update(parsed_taste)
    if "source" not in new_taste:
        new_taste["source"] = "Parsed from vault Overview.md (🎯 Projects/🎬 TV and Movie Concierge/Overview.md). Re-synced by the daily scheduled task."

    snapshots_changed = 0
    snapshots_changed += int(write_snapshot_if_changed(repo_root / "data" / "watched.json", new_watched_json, synced_at))
    snapshots_changed += int(write_snapshot_if_changed(repo_root / "data" / "watchlist.json", new_watchlist_json, synced_at))
    snapshots_changed += int(write_snapshot_if_changed(repo_root / "data" / "not-interested.json", new_notint_json, synced_at))
    snapshots_changed += int(write_snapshot_if_changed(taste_path, new_taste, synced_at))

    # Step 4: reset pending (only if it had entries — keep file untouched on no-op
    # so idempotent runs produce no diff)
    if entries:
        write_json(pending_path, {
            "format_note": pending.get(
                "format_note",
                "Append-only inbox written by the web app. Daily scheduled task drains entries[] into vault MD files and resets to []. Schemas: {kind: 'watched', title, rating, tags, date, ts} | {kind: 'pin'|'unpin'|'hide'|'unhide', title, ts}",
            ),
            "entries": [],
        })

    # Step 5: git status / commit / push if anything changed
    status = run(["git", "status", "--porcelain", "data/"], cwd=repo_root)
    dirty = bool(status.stdout.strip())

    if dirty:
        run(["git", "add", "data/"], cwd=repo_root)
        n = len(drained)
        commit_msg = f"Daily sync: drained {n} pending, refreshed {snapshots_changed} snapshots"
        run(["git", "commit", "-m", commit_msg], cwd=repo_root)
        try:
            run(["git", "push"], cwd=repo_root)
        except subprocess.CalledProcessError as e:
            print(f"WARN: git push failed (commit was made locally): {e.stderr}", file=sys.stderr)

    # Step 6: summary
    print(f"Synced {len(drained)} web actions to vault; {'pushed' if dirty else 'no changes to push'}.")
    if drained:
        for d in drained:
            print(f"  + {d}")
    if skipped:
        for s in skipped:
            print(f"  ~ skipped: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

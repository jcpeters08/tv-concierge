#!/usr/bin/env python3
"""TVmaze episode schedules — a second source for weekly trackers. Pure stdlib.

Use it to confirm "Ep N drops <day>" claims alongside JustWatch: it lists
every episode of a season with its air date and title.

Usage:
  python3 scripts/tvmaze.py "The Lowdown" --season 2 --year 2025
  python3 scripts/tvmaze.py "Phony" --year 2026

Notes: dates are the original broadcast/stream date; FX and ABC shows reach
Hulu the same night or the next day. --year (premiere year, +/-1) picks the
right show when several share a name.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request


def get(url: str):
    err = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            err = e
            time.sleep(1.5)
    return {"error": str(err)}


def schedule(query: str, season: int | None = None, year: int | None = None) -> str:
    results = get("https://api.tvmaze.com/search/shows?q=" + urllib.parse.quote(query))
    if isinstance(results, dict):
        return f"!! {query}: {results}"
    show = None
    for r in results:
        s = r["show"]
        if year and s.get("premiered") and abs(int(s["premiered"][:4]) - year) > 1:
            continue
        show = s
        break
    if not show:
        names = [(r["show"]["name"], r["show"].get("premiered")) for r in results[:4]]
        return f"!! {query}: no match among {names}"
    eps = get(f"https://api.tvmaze.com/shows/{show['id']}/episodes?specials=0")
    if isinstance(eps, dict):
        return f"!! {query}: {eps}"
    seasons = sorted({e["season"] for e in eps})
    want = season or (seasons[-1] if seasons else None)
    home = (show.get("network") or show.get("webChannel") or {}).get("name")
    lines = [f"## {query} => {show['name']} (premiered {show.get('premiered')}) "
             f"status={show.get('status')} on={home} seasons={seasons}"]
    for e in eps:
        if e["season"] == want:
            lines.append(f"   S{e['season']}E{e['number']} {e.get('airdate')} {e.get('airtime') or ''} "
                         f"'{e['name']}' {e.get('runtime')}m")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("query")
    ap.add_argument("--season", type=int, help="default: latest season")
    ap.add_argument("--year", type=int, help="premiere year, to disambiguate")
    args = ap.parse_args()
    print(schedule(args.query, args.season, args.year))
    return 0


if __name__ == "__main__":
    sys.exit(main())

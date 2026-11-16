#!/usr/bin/env python3
"""Rotten Tomatoes scores for the recommendation refresh. Pure stdlib.

Reads the Tomatometer, review count and audience score straight from an RT
page so "why" text never quotes a stale number.

Usage:
  python3 scripts/rt.py tv/slow_horses/s06 m/black_bag tv/phony/s01

Each argument is the path part of an RT URL: tv/<slug>/s<NN> for a season
(season-specific scores are what the briefing quotes), tv/<slug> for a whole
series, m/<slug> for a film. Slugs have to be guessed (lowercase, underscores,
sometimes a year suffix like m/companion_2025 or tv/the_lowdown_2025); a 404
means the slug is wrong, not that the title has no score. "TM=None (0 reviews)"
means the page exists but has no Tomatometer yet.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SCORECARD = re.compile(r'<script[^>]*id="media-scorecard-json"[^>]*>(.*?)</script>', re.S)


def score(path: str) -> str:
    url = "https://www.rottentomatoes.com/" + path.lstrip("/")
    html, err = None, None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", "ignore")
            break
        except Exception as e:
            err = e
            time.sleep(1)
    if html is None:
        return f"{path}: ERR {err}"
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    name = title.group(1).strip().replace(" | Rotten Tomatoes", "")[:60] if title else ""
    m = SCORECARD.search(html)
    if not m:
        return f"{path}: no scorecard on page ({name})"
    card = json.loads(m.group(1))
    critics = card.get("criticsScore") or {}
    audience = card.get("audienceScore") or {}
    return (f"{path}: TM={critics.get('scorePercent')} ({critics.get('reviewCount')} reviews, "
            f"certified={critics.get('certified')}) AUD={audience.get('scorePercent')} "
            f"({audience.get('bandedRatingCount')}) | {name}")


def main() -> int:
    paths = sys.argv[1:]
    if not paths or paths[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if paths else 2
    with ThreadPoolExecutor(4) as ex:
        for line in ex.map(score, paths):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Date lint for data/recommendations.json. Run before committing any refresh.

Catches recycled news: a search returns last year's article ("Episodes 1–2
drop Fri Nov 7"), the refresh stamps this year on it, and a finished show is
presented as new or airing weekly. On Nov 18, 2026 that shipped Pluribus as
"New Ep 5 Live" a year after its season ended, along with The Beast in Me,
Train Dreams, Gachiakuta, Jujutsu Kaisen S3 and Chad Powers S2.

Usage:
  python3 scripts/check_recs.py            # offline: weekday/year mismatches
  python3 scripts/check_recs.py --online   # + TVmaze/JustWatch freshness
  python3 scripts/check_recs.py --online --as-of 2026-11-18 path/to/recs.json

Checks:
  * Weekday mismatch (offline, every section): "Fri Nov 7, 2026" is a
    Saturday, so the text came from 2025. Any hit means the date is wrong.
  * Freshness (--online; hero, new_this_week, critics, and bench entries that
    claim weekly / "Ep N"): for series, TVmaze's
    episode dates for the named season (latest if none). STALE when nothing
    aired in the last 45 days and nothing is due in the next 14, so it's
    catalog and belongs in bench[] with its real dates. FINISHED when the
    card says weekly/new-ep but the season has no upcoming episode. For
    films, the earliest JustWatch subscription offer on Jon's platforms;
    STALE when that's older than 45 days.
Exit status is 1 when anything is flagged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path

FRESH_DAYS = 45      # a season that ended within this window is still "new"
SOON_DAYS = 14       # an episode/premiere due within this window counts as current
ACTIVE = ("new_this_week", "critics")

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DATE_RE = re.compile(
    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,?\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b(?:,?\s+(\d{4}))?")
WEEKLY_RE = re.compile(r"\bweekly\b|\bnew ep\b|\bep \d+\b", re.I)


def claims_weekly(entry: dict) -> bool:
    """Title or format presents the show as dropping episodes now."""
    return bool(WEEKLY_RE.search(entry.get("title") or "") or WEEKLY_RE.search(entry.get("format") or ""))


def weekday_mismatches(text: str, default_year: int) -> list[dict]:
    """Dates like 'Fri Nov 7, 2026' whose weekday doesn't fit the year."""
    hits = []
    for m in DATE_RE.finditer(text or ""):
        dow, mon, day, year = m.groups()
        years = [int(year)] if year else [default_year, default_year + 1]
        try:
            dates = [dt.date(y, MONTHS[mon.lower()], int(day)) for y in years]
        except ValueError:
            continue
        if any(d.strftime("%a") == dow for d in dates):
            continue
        y0 = years[0]
        fits = [y for y in range(y0 - 3, y0 + 4)
                if dt.date(y, MONTHS[mon.lower()], int(day)).strftime("%a") == dow]
        hits.append({"text": m.group(0), "actual": dates[0].strftime("%a"),
                     "year": y0, "fits": [y for y in fits if abs(y - y0) == 1] or fits})
    return hits


def base_title(title: str) -> tuple[str, int | None, int | None]:
    """'The Rings of Power S3 — Now 2 Eps Deep' -> ('The Rings of Power', 3, None)."""
    t = re.split(r"\s+[—–]\s+|\s+-\s+", title, maxsplit=1)[0]
    year = None
    m = re.search(r"\((\d{4})\)", t)
    if m:
        year = int(m.group(1))
    t = re.sub(r"\s*\([^)]*\)", "", t).strip()
    season = None
    m = re.search(r"\s+S(\d+)(?:\s*(?:Part|Pt\.?)\s*\d+)?$", t)
    if m:
        season = int(m.group(1))
        t = t[:m.start()].strip()
    return t, season, year


def freshness(airdates: list[dt.date], as_of: dt.date, claims_weekly: bool) -> dict:
    """Classify a season by its episode air dates relative to as_of."""
    aired = sorted(d for d in airdates if d <= as_of)
    upcoming = sorted(d for d in airdates if d > as_of)
    last = aired[-1] if aired else None
    nxt = upcoming[0] if upcoming else None
    out = {"last": last, "next": nxt}
    recent = last is not None and (as_of - last).days <= FRESH_DAYS
    soon = nxt is not None and (nxt - as_of).days <= SOON_DAYS
    if not recent and not soon:
        # Nothing current. With a far-off next episode it's an upcoming alert.
        out["status"] = "upcoming" if nxt else "stale"
    elif claims_weekly and not upcoming:
        out["status"] = "finished"
    else:
        out["status"] = "ok"
    return out


# ------------------------------------------------------------ online lookups

def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", s.lower().replace("&", "and"))
    return re.sub(r"^the ", "", re.sub(r"\s+", " ", s).strip())


def tvmaze_airdates(name: str, season: int | None, year: int | None):
    import tvmaze
    results = tvmaze.get("https://api.tvmaze.com/search/shows?q=" + urllib.parse.quote(name))
    if isinstance(results, dict):
        return None, f"TVmaze error {results}"
    want = _norm(name)
    show = None
    for r in results:
        s = r["show"]
        names = {_norm(s["name"]), _norm(s["name"].split(":")[-1])}
        if want not in names:
            continue
        if year and s.get("premiered") and abs(int(s["premiered"][:4]) - year) > 1:
            continue
        show = s
        break
    if not show:
        top = [r["show"]["name"] for r in results[:3]]
        return None, f"no exact TVmaze match (top: {top}) — check by hand with jw.py"
    eps = tvmaze.get(f"https://api.tvmaze.com/shows/{show['id']}/episodes?specials=0")
    if isinstance(eps, dict):
        return None, f"TVmaze error {eps}"
    seasons = sorted({e["season"] for e in eps})
    if not seasons:
        return None, "TVmaze lists no episodes"
    s = season or seasons[-1]
    dates = [dt.date.fromisoformat(e["airdate"]) for e in eps
             if e["season"] == s and e.get("airdate")]
    if not dates:
        return None, f"TVmaze has no dated episodes for S{s}"
    return (s, dates), None


def jw_film_since(name: str, year: int | None):
    import jw
    o = jw.check({"label": name, "q": name, "type": "MOVIE", "year": year})
    if "err" in o:
        return None, f"JustWatch: {o['err']}"
    since = [v["since"] for (pkg, _), v in o["offers"].items() if pkg in jw.MINE and v.get("since")]
    if not since:
        return None, f"JustWatch: no subscription offer on Jon's platforms ({o['title']} {o['year']})"
    return dt.date.fromisoformat(min(since)[:10]), None


def online_check(entry: dict, as_of: dt.date) -> tuple[str, str]:
    name, season, year = base_title(entry["title"])
    fmt = (entry.get("format") or "").lower()
    if "film" in fmt or "movie" in fmt:
        since, err = jw_film_since(name, year)
        if err:
            return "WARN", err
        age = (as_of - since).days
        if age > FRESH_DAYS:
            return "STALE", f"on a subscription since {since} ({age} days) — catalog, move to bench"
        return "OK", f"subscription offer since {since}"
    found, err = tvmaze_airdates(name, season, year)
    if err:
        return "WARN", err
    s, dates = found
    v = freshness(dates, as_of, claims_weekly(entry))
    span = f"S{s} {min(dates)} → {max(dates)}; last aired {v['last']}, next {v['next']}"
    if v["status"] == "stale":
        return "STALE", f"{span} — season is over, move to bench with real dates"
    if v["status"] == "finished":
        return "FINISHED", f"{span} — card claims weekly/new ep but the season is done"
    if v["status"] == "upcoming":
        return "WARN", f"{span} — nothing due within {SOON_DAYS} days; belongs in upcoming_alerts"
    return "OK", span


# ----------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parent.parent
                                                  / "data" / "recommendations.json"))
    ap.add_argument("--online", action="store_true", help="also check TVmaze/JustWatch freshness")
    ap.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    args = ap.parse_args()
    recs = json.loads(Path(args.path).read_text())
    flagged = 0

    sections = [("hero", [recs["hero"]] if recs.get("hero") else [])]
    sections += [(k, recs.get(k) or []) for k in ("new_this_week", "critics", "bench", "upcoming_alerts")]
    for sec, entries in sections:
        for e in entries:
            for field in ("title", "date", "format", "about", "why"):
                for h in weekday_mismatches(e.get(field) or "", args.as_of.year):
                    flagged += 1
                    print(f"WEEKDAY  {sec:15} {e['title'][:55]!r} {field}: '{h['text']}' is a "
                          f"{h['actual']} in {h['year']} — weekday fits {h['fits']}, likely recycled news")

    if args.online:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        for sec, entries in sections:
            if sec == "upcoming_alerts":
                continue
            for e in entries:
                # Bench is catalog by design; only its weekly trackers need dates checked.
                if sec == "bench" and not claims_weekly(e):
                    continue
                status, msg = online_check(e, args.as_of)
                if status in ("STALE", "FINISHED"):
                    flagged += 1
                print(f"{status:8} {sec:15} {e['title'][:55]!r}: {msg}")

    print(f"\n{flagged} flagged" + ("" if args.online else " (offline; add --online for freshness)"))
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())

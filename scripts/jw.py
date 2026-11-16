#!/usr/bin/env python3
"""JustWatch US lookups for the recommendation refresh. Pure stdlib.

JustWatch is the ground truth for "streaming on X right now" (see CLAUDE.md).
This talks to the same GraphQL endpoint the website uses, so a whole refresh
can be verified from one script instead of one web fetch per title.

Usage:
  python3 scripts/jw.py title "Slow Horses" --type SHOW [--year 2022] [--season 6] [--eps]
  python3 scripts/jw.py check titles.json [--eps]
  python3 scripts/jw.py new 2026-11-01 2026-11-15 [--pk nfx,hlu] [--min-votes 1500] [--all]
  python3 scripts/jw.py upcoming [--pk nfx,atp]

`check` takes a JSON list of {"label", "q" or "path", "type", "year", "season"}:
  [{"label": "Phony", "q": "Phony", "type": "SHOW", "year": 2026},
   {"label": "Looper", "path": "/us/movie/looper"}]

Reading the output:
  * Only subscription offers on Jon's platforms are shown as MINE. An offer
    ending is printed as LEAVES <date> and comes from that offer's own
    availableToTime, so it is safe to call "last chance" (rent/buy offers are
    never included).
  * Per-episode "live" dates are the day JustWatch saw the episode, in UTC,
    so they are often one day after the US release date.
  * Known gap: Crunchyroll simulcast seasons sometimes appear only under
    "Crunchyroll Amazon Channel" (listed under "other").
  * Introspection is disabled; a wrong field returns HTTP 422 with a message
    that names the right one.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "https://apis.justwatch.com/graphql"

# Package short codes for the new/upcoming feeds.
PACKAGES = ["nfx", "atp", "amp", "cru", "dnp", "hlu"]
# Offer clearNames that count as Jon's subscriptions.
MINE = {"Netflix", "Apple TV", "Apple TV+", "Amazon Prime Video", "Crunchyroll",
        "Disney Plus", "Hulu"}
SUBSCRIPTION = ("FLATRATE", "ADS", "FREE")
# Feed rows whose genres all fall in this set are dropped unless --all.
OFF_TASTE = {"doc", "rly", "spt", "msc", "fml", "rma", "eur", "war", "hst", "wsn"}


def gql(query: str, variables: dict, retries: int = 3) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(URL, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:1500]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return {"http_error": e.code, "body": detail}
        except Exception as e:  # network hiccup
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return {"error": str(e)}
    return {"error": "unreachable"}


# ------------------------------------------------------------ title checks

SEARCH_Q = """query($q:String!,$t:[ObjectType!]){popularTitles(country:US,first:6,
 filter:{searchQuery:$q,objectTypes:$t}){edges{node{objectType
 content(country:US,language:"en"){title originalReleaseYear fullPath}}}}}"""

OFFER = "monetizationType elementCount availableToTime dateCreated package{clearName}"
CONTENT = ("title originalReleaseYear runtime fullPath genres{shortName} "
           "scoring{tomatoMeter imdbScore imdbVotes} "
           "upcomingReleases{releaseDate releaseType package{clearName}}")
DETAIL_Q = f"""query($p:String!){{urlV2(fullPath:$p){{node{{__typename
 ... on Movie{{content(country:US,language:"en"){{{CONTENT}}}
   offers(country:US,platform:WEB){{{OFFER}}}}}
 ... on Show{{content(country:US,language:"en"){{{CONTENT}}}
   offers(country:US,platform:WEB){{{OFFER}}}
   seasons{{totalEpisodeCount content(country:US,language:"en"){{seasonNumber fullPath}}}}}}
}}}}}}"""
SEASON_Q = f"""query($p:String!){{urlV2(fullPath:$p){{node{{... on Season{{
 content(country:US,language:"en"){{upcomingReleases{{releaseDate releaseType package{{clearName}}}}}}
 offers(country:US,platform:WEB){{{OFFER}}}
 episodes{{content(country:US,language:"en"){{episodeNumber title runtime}}
   offers(country:US,platform:WEB){{monetizationType dateCreated package{{clearName}}}}}}
}}}}}}}}"""


def search(q: str, typ: str | None = None, year: int | None = None):
    r = gql(SEARCH_Q, {"q": q, "t": [typ] if typ else None})
    try:
        edges = r["data"]["popularTitles"]["edges"]
    except Exception:
        return None, str(r)[:200]
    best, best_score = None, -1
    for i, edge in enumerate(edges):
        c = edge["node"]["content"]
        score = 10 - i
        if c["title"].lower() == q.lower():
            score += 20
        if year and c["originalReleaseYear"] and abs(c["originalReleaseYear"] - year) <= 1:
            score += 30
        if score > best_score:
            best, best_score = c, score
    alts = [(e["node"]["content"]["title"], e["node"]["content"]["originalReleaseYear"])
            for e in edges][:4]
    return (best["fullPath"] if best else None), alts


def _subs(offers) -> dict:
    """Collapse offers to one row per (package, monetization), subscriptions only."""
    out: dict = collections.OrderedDict()
    for o in offers or []:
        if o["monetizationType"] not in SUBSCRIPTION:
            continue
        key = (o["package"]["clearName"], o["monetizationType"])
        row = {"ec": o.get("elementCount"), "to": (o.get("availableToTime") or "")[:10],
               "since": o.get("dateCreated")}
        if key not in out or (row["ec"] or 0) > (out[key]["ec"] or 0):
            out[key] = row
    return out


def _ups(content) -> list:
    return [(u["releaseDate"], u["releaseType"][:3], (u.get("package") or {}).get("clearName"))
            for u in (content.get("upcomingReleases") or [])]


def check(item: dict) -> dict:
    label, path, alts = item["label"], item.get("path"), None
    if not path:
        path, alts = search(item["q"], item.get("type"), item.get("year"))
    if not path:
        return {"label": label, "err": "no match", "alts": alts}
    r = gql(DETAIL_Q, {"p": path})
    try:
        node = r["data"]["urlV2"]["node"]
        c = node["content"]
    except Exception:
        return {"label": label, "err": str(r)[:300], "path": path}
    out = {"label": label, "path": path, "title": c["title"], "year": c["originalReleaseYear"],
           "kind": node["__typename"], "tm": c["scoring"]["tomatoMeter"],
           "imdb": c["scoring"]["imdbScore"], "runtime": c["runtime"],
           "genres": [g["shortName"] for g in c["genres"]], "alts": alts,
           "offers": _subs(node.get("offers")), "up": _ups(c)}
    if node["__typename"] != "Show":
        return out
    seasons = sorted(node["seasons"], key=lambda s: s["content"]["seasonNumber"])
    out["seasons"] = [(s["content"]["seasonNumber"], s["totalEpisodeCount"]) for s in seasons]
    want = item.get("season") or (seasons[-1]["content"]["seasonNumber"] if seasons else None)
    match = [s for s in seasons if s["content"]["seasonNumber"] == want]
    if not match:
        return out
    sr = gql(SEASON_Q, {"p": match[0]["content"]["fullPath"]})
    try:
        sd = sr["data"]["urlV2"]["node"]
    except Exception:
        out["eps_err"] = str(sr)[:200]
        return out
    out["season"] = want
    out["s_offers"] = _subs(sd.get("offers"))
    out["s_up"] = _ups(sd["content"])
    eps = []
    for e in sorted(sd["episodes"], key=lambda e: e["content"]["episodeNumber"]):
        mine = [(o["dateCreated"], o["package"]["clearName"]) for o in e["offers"]
                if o["monetizationType"] in SUBSCRIPTION and o["package"]["clearName"] in MINE]
        eps.append((e["content"]["episodeNumber"], e["content"]["title"],
                    e["content"]["runtime"], min(mine)[0] if mine else None,
                    sorted({p for _, p in mine})))
    out["eps"] = eps
    return out


def _offer_str(offers: dict) -> str:
    parts = [f"{pkg}/{mon[:4]} ec={v['ec']} since={v['since']}"
             + (f" LEAVES {v['to']}" if v["to"] else "")
             for (pkg, mon), v in offers.items() if pkg in MINE]
    return "; ".join(parts) or "NONE-ON-MY-PLATFORMS"


def fmt(o: dict, full_eps: bool = False) -> str:
    if "err" in o:
        return f"!! {o['label']}: {o['err']} alts={o.get('alts')}"
    other = sorted({pkg for pkg, _ in o["offers"] if pkg not in MINE})
    lines = [f"## {o['label']} => {o['title']} ({o['year']}) [{o['kind']}] {o['path']} "
             f"tm={o['tm']} imdb={o['imdb']} rt={o['runtime']} g={','.join(o['genres'])}",
             f"   MINE: {_offer_str(o['offers'])}"
             + (f"   | other: {', '.join(other[:6])}" if other else "")]
    if o.get("up"):
        lines.append(f"   UPCOMING: {o['up']}")
    if o["kind"] == "Show":
        lines.append(f"   seasons={o.get('seasons')}")
        if "eps" in o:
            eps = o["eps"]
            live = [e for e in eps if e[3]]
            rts = [e[2] for e in eps if e[2]]
            s_other = sorted({pkg for pkg, _ in o.get("s_offers", {}) if pkg not in MINE})[:4]
            lines.append(f"   S{o['season']}: listed={len(eps)} live={len(live)} "
                         f"avg_rt={round(sum(rts) / len(rts)) if rts else None} "
                         f"[{_offer_str(o.get('s_offers', {}))}]"
                         + (f" other={s_other}" if s_other else "")
                         + (f" next={o['s_up']}" if o.get("s_up") else ""))
            shown = eps if full_eps else live[-3:] + [e for e in eps if not e[3]][:2]
            for num, title, rt, since, pkgs in shown:
                lines.append(f"     E{num} '{title}' {rt}m live={since} {','.join(pkgs)}")
    return "\n".join(lines)


# ------------------------------------------------------- new / upcoming

FEED_Q = """query($d:Date!,$pk:[String!],$after:String,$pt:NewPageType!){
 newTitles(country:US,date:$d,first:100,after:$after,filter:{packages:$pk},
   priceDrops:false,pageType:$pt){
  pageInfo{endCursor hasNextPage}
  edges{newOffer(platform:WEB){monetizationType availableToTime package{clearName} elementCount newElementCount}
   node{__typename
    ... on Movie{content(country:US,language:"en"){title originalReleaseYear fullPath runtime shortDescription
      scoring{tomatoMeter imdbScore imdbVotes} genres{shortName}
      upcomingReleases{releaseDate releaseType package{clearName}}}}
    ... on Season{totalEpisodeCount
      content(country:US,language:"en"){seasonNumber fullPath
        upcomingReleases{releaseDate releaseType package{clearName}}}
      show{content(country:US,language:"en"){title originalReleaseYear runtime shortDescription
        scoring{tomatoMeter imdbScore imdbVotes} genres{shortName}}}}}}}}"""


def feed(day: str, pk: str, page_type: str) -> list:
    """One day's 'new' rows for a package, or the whole coming-soon list (UPCOMING)."""
    rows, after = [], None
    while True:
        r = gql(FEED_Q, {"d": day, "pk": [pk], "after": after, "pt": page_type})
        try:
            nt = r["data"]["newTitles"]
        except Exception:
            rows.append({"err": str(r)[:300], "date": day, "pk": pk})
            return rows
        for edge in nt["edges"]:
            node, offer = edge["node"], edge.get("newOffer") or {}
            if node["__typename"] == "Movie":
                c = node["content"]
                row = {"kind": "movie", "path": c["fullPath"]}
            else:
                c = node["show"]["content"]
                row = {"kind": "season", "season": node["content"]["seasonNumber"],
                       "path": node["content"]["fullPath"], "total": node["totalEpisodeCount"]}
            row.update(title=c["title"], year=c["originalReleaseYear"], runtime=c["runtime"],
                       tm=c["scoring"]["tomatoMeter"], imdb=c["scoring"]["imdbScore"],
                       votes=c["scoring"]["imdbVotes"], desc=c["shortDescription"],
                       genres=[g["shortName"] for g in c["genres"]],
                       up=_ups(node["content"]), date=day, pk=pk,
                       mon=offer.get("monetizationType"),
                       pkg=(offer.get("package") or {}).get("clearName"),
                       ec=offer.get("elementCount"), to=offer.get("availableToTime"))
            rows.append(row)
        if not nt["pageInfo"]["hasNextPage"]:
            return rows
        after = nt["pageInfo"]["endCursor"]


def _what(x: dict) -> str:
    if x["kind"] == "season":
        return f"S{x['season']} {x.get('ec')}/{x.get('total')}ep"
    return f"movie {x.get('runtime')}m"


def cmd_new(args) -> None:
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    jobs, d = [], start
    while d <= end:
        jobs += [(d.isoformat(), pk, "NEW") for pk in args.pk]
        d += datetime.timedelta(days=1)
    with ThreadPoolExecutor(6) as ex:
        rows = [x for chunk in ex.map(lambda j: feed(*j), jobs) for x in chunk]
    for x in rows:
        if "err" in x:
            print("!!", x, file=sys.stderr)
    grouped = collections.defaultdict(list)
    for x in rows:
        if "err" not in x and x.get("mon") == "FLATRATE":
            grouped[(x["pk"], x["title"], x.get("season"), x["kind"])].append(x)
    out = []
    for (pk, title, _, _), xs in grouped.items():
        x = xs[-1]
        genres = set(x["genres"])
        if not args.all and (not genres or genres <= OFF_TASTE):
            continue
        if (x["votes"] or 0) < args.min_votes:
            continue
        days = sorted({y["date"] for y in xs})
        out.append(f"{pk}|{days[0][5:]}|{title} ({x['year']}) {_what(x)}"
                   + (f" x{len(days)} last {days[-1][5:]}" if len(days) > 1 else "")
                   + f"|tm={x['tm']} imdb={x['imdb']} votes={x['votes']}|{','.join(x['genres'])}|"
                   + (f"LEAVES {x['to'][:10]}" if x.get("to") else "")
                   + f"|{x['path']}|{(x['desc'] or '')[:90]}")
    print("\n".join(sorted(out)))
    print(f"{len(out)} titles ({len(rows)} raw rows)", file=sys.stderr)


def cmd_upcoming(args) -> None:
    today = datetime.date.today().isoformat()
    with ThreadPoolExecutor(6) as ex:
        rows = [x for chunk in ex.map(lambda pk: feed(today, pk, "UPCOMING"), args.pk)
                for x in chunk]
    out, seen = [], set()
    for x in rows:
        if "err" in x:
            print("!!", x, file=sys.stderr)
            continue
        key = (x["pk"], x["title"], x.get("season"))
        if key in seen:
            continue
        seen.add(key)
        digital = [u[0] for u in x["up"] if u[1] == "DIG"] or [u[0] for u in x["up"]]
        when = min(digital) if digital else "?"
        total = f"S{x['season']}/{x.get('total')}ep" if x["kind"] == "season" else f"movie {x.get('runtime')}m"
        out.append(f"{when}|{x['pk']}|{x['title']} ({x['year']}) {total}|tm={x['tm']} imdb={x['imdb']}"
                   f"|{','.join(x['genres'])}|{x['path']}|{(x['desc'] or '')[:90]}")
    print("\n".join(sorted(out)))


def cmd_check(args) -> None:
    items = json.loads(open(args.file).read())
    with ThreadPoolExecutor(5) as ex:
        for result in ex.map(check, items):
            print(fmt(result, args.eps))


def cmd_title(args) -> None:
    item = {"label": args.query, "type": args.type, "year": args.year, "season": args.season}
    item["path" if args.query.startswith("/us/") else "q"] = args.query
    print(fmt(check(item), args.eps))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("title", help="check one title (search text or a /us/... path)")
    t.add_argument("query")
    t.add_argument("--type", choices=["SHOW", "MOVIE"])
    t.add_argument("--year", type=int)
    t.add_argument("--season", type=int)
    t.add_argument("--eps", action="store_true", help="list every episode")
    t.set_defaults(fn=cmd_title)

    c = sub.add_parser("check", help="check a JSON list of titles")
    c.add_argument("file")
    c.add_argument("--eps", action="store_true", help="list every episode")
    c.set_defaults(fn=cmd_check)

    pk = lambda s: s.split(",")  # noqa: E731
    n = sub.add_parser("new", help="subscription arrivals per platform for a date range")
    n.add_argument("start")
    n.add_argument("end")
    n.add_argument("--pk", type=pk, default=PACKAGES, help="comma list, default all six")
    n.add_argument("--all", action="store_true", help="keep docs/reality/kids rows too")
    n.add_argument("--min-votes", type=int, default=0,
                   help="drop rows with fewer IMDb votes (cuts catalog noise; hides brand-new titles)")
    n.set_defaults(fn=cmd_new)

    u = sub.add_parser("upcoming", help="JustWatch's coming-soon list per platform")
    u.add_argument("--pk", type=pk, default=PACKAGES)
    u.set_defaults(fn=cmd_upcoming)

    args = ap.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

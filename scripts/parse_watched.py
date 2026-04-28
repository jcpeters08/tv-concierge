"""Markdown <-> JSON helpers for the TV Concierge sync.

Pure stdlib. Used by sync.py to:
- Parse vault MD (Watched, Watchlist, Not-Interested, Overview) into snapshot JSON.
- Format new watched entries back into the canonical vault line shape.
- Append/remove title lines in title-list MD files (Watchlist, Not-Interested).
"""

from __future__ import annotations

import re
from typing import Iterable

EN_DASH = "\u2013"  # –
STAR = "\u2b50"     # ⭐

WATCHED_LINE_RE = re.compile(
    r"^- (?P<title>.+?) "
    + EN_DASH
    + r" (?P<rating>\d+(?:\.\d+)?)"
    + STAR
    + r" "
    + EN_DASH
    + r" \"(?P<tags>.*?)\" \((?P<date>\d{4}-\d{2}-\d{2})\)\s*$"
)

STAR_HEADER_RE = re.compile(r"^##\s*(\d+(?:\.\d+)?)\s*" + STAR)
RECENTLY_LOGGED_HEADER = "## \U0001f195 Recently Logged (pending sort)"


def format_watched_line(title: str, rating: float, tags: str, date: str) -> str:
    """Render a watched entry as the canonical vault MD line."""
    rating_str = f"{int(rating)}" if float(rating).is_integer() else f"{rating}"
    return f"- {title} {EN_DASH} {rating_str}{STAR} {EN_DASH} \"{tags}\" ({date})"


def _split_tags(tags_str: str) -> list[str]:
    return [t.strip() for t in tags_str.split(",") if t.strip()]


def parse_watched_md(text: str) -> dict:
    """Parse Watched.md into the watched.json shape.

    Returns {entries: [...], known_favorites: [...], known_dislikes: [...]}.
    """
    entries: list[dict] = []
    known_favorites: list[str] = []
    known_dislikes: list[str] = []

    section: str | None = None  # 'rated' | 'favorites' | 'dislikes' | None
    seen_titles: set[str] = set()

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if STAR_HEADER_RE.match(line):
                section = "rated"
            elif "known favorites" in heading:
                section = "favorites"
            elif "known dislikes" in heading:
                section = "dislikes"
            elif heading.startswith("\U0001f195 recently logged") or "recently logged" in heading:
                section = "rated"
            else:
                section = None
            continue
        if section == "rated":
            m = WATCHED_LINE_RE.match(line)
            if not m:
                continue
            title = m.group("title").strip()
            rating_raw = m.group("rating")
            rating = float(rating_raw)
            if rating.is_integer():
                rating = int(rating)
            tags = m.group("tags")
            date = m.group("date")
            if title in seen_titles:
                continue
            seen_titles.add(title)
            entries.append(
                {
                    "title": title,
                    "rating": rating,
                    "tags": tags,
                    "tags_array": _split_tags(tags),
                    "date": date,
                }
            )
        elif section == "favorites":
            if line.startswith("- "):
                known_favorites.append(line[2:].strip())
        elif section == "dislikes":
            if line.startswith("- "):
                known_dislikes.append(line[2:].strip())

    return {
        "entries": entries,
        "known_favorites": known_favorites,
        "known_dislikes": known_dislikes,
    }


def watched_titles_in(text: str) -> set[str]:
    """All titles already present in Watched.md (any rated section)."""
    out: set[str] = set()
    for raw in text.splitlines():
        m = WATCHED_LINE_RE.match(raw.rstrip())
        if m:
            out.add(m.group("title").strip())
    return out


def parse_titles_md(text: str) -> list[str]:
    """Parse a Watchlist.md or Not-Interested.md file into a flat titles list.

    Skips the YAML frontmatter and the H1/intro paragraph; collects every
    `- Title` bullet that follows.
    """
    titles: list[str] = []
    in_frontmatter = False
    seen_frontmatter_close = False
    started = False
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        in_frontmatter = True
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if i == 0 and line.strip() == "---":
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
                seen_frontmatter_close = True
            continue
        if line.startswith("- "):
            started = True
            title = line[2:].strip()
            if title and title not in titles:
                titles.append(title)
    _ = seen_frontmatter_close, started  # appease linters
    return titles


def append_title(text: str, title: str) -> tuple[str, bool]:
    """Append `- {title}` if not already present. Returns (new_text, changed)."""
    existing = set(parse_titles_md(text))
    if title in existing:
        return text, False
    if not text.endswith("\n"):
        text = text + "\n"
    return text + f"- {title}\n", True


def remove_title(text: str, title: str) -> tuple[str, bool]:
    """Remove a `- {title}` line. Returns (new_text, changed)."""
    target = f"- {title}"
    out_lines: list[str] = []
    changed = False
    for raw in text.splitlines():
        if raw.rstrip() == target and not changed:
            changed = True
            continue
        out_lines.append(raw)
    new_text = "\n".join(out_lines)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, changed


def append_watched_entry(text: str, line: str) -> str:
    """Append `line` under `## 🆕 Recently Logged (pending sort)`.

    Creates the section at the end of the file if absent.
    """
    if not text.endswith("\n"):
        text = text + "\n"
    if RECENTLY_LOGGED_HEADER in text:
        # Append the new entry at the very end of the file (the section may have
        # been the last thing in the file or be followed by other H2s; either way
        # appending under the section keeps things readable when this is the
        # final section, which is how we create it).
        lines = text.splitlines()
        # Find the section header
        idx = None
        for i, raw in enumerate(lines):
            if raw.rstrip() == RECENTLY_LOGGED_HEADER:
                idx = i
                break
        if idx is None:
            return text + f"\n{RECENTLY_LOGGED_HEADER}\n{line}\n"
        # Find end of section (next ## header or EOF)
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        # Insert before `end`, trimming trailing blank lines inside section
        insert_at = end
        while insert_at - 1 > idx and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        lines.insert(insert_at, line)
        new_text = "\n".join(lines)
        if not new_text.endswith("\n"):
            new_text += "\n"
        return new_text
    # No section yet — create at end
    return text + f"\n{RECENTLY_LOGGED_HEADER}\n\n{line}\n"


# ---- Overview.md (Taste Profile) parser -------------------------------------

_BULLET_LABEL_RE = re.compile(r"^-\s+\*\*(?P<label>[^*]+):\*\*\s*(?P<value>.+?)\s*$")


def _split_csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def parse_overview_md(text: str) -> dict:
    """Best-effort parse of vault Overview.md.

    Returns a partial dict containing whichever of these fields could be
    extracted: top_genres, lower_priority, loved, disliked, viewing_habit,
    language. Unparseable fields are simply omitted; sync.py merges this
    over the prior taste-profile.json so other fields (like derived signals)
    survive untouched.
    """
    out: dict = {}
    section: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if "taste profile" in heading:
                section = "taste"
            elif heading == "notes":
                section = "notes"
            else:
                section = None
            continue
        if section == "taste":
            m = _BULLET_LABEL_RE.match(line)
            if not m:
                continue
            label = m.group("label").strip().lower()
            value = m.group("value").strip()
            if label in ("priorities", "top genres"):
                out["top_genres"] = _split_csv(value.replace(" & ", ", "))
            elif label == "lower priority":
                out["lower_priority"] = _split_csv(value)
            elif label == "loved":
                out["loved"] = _split_csv(value)
            elif label == "disliked":
                out["disliked"] = _split_csv(value)
        elif section == "notes":
            if line.startswith("- "):
                body = line[2:].strip()
                low = body.lower()
                if low.startswith("viewing habit:"):
                    out["viewing_habit"] = body.split(":", 1)[1].strip()
                elif low.startswith("open to any language"):
                    out["language"] = body
    return out


# ---- YAML headers for new title-list files ---------------------------------

WATCHLIST_HEADER = """---
type: log
tags: [entertainment, watchlist]
aliases: [Watchlist]
---
# \U0001f4cc Watchlist

Titles I want to watch — pinned to the top of the briefing.

"""

NOT_INTERESTED_HEADER = """---
type: log
tags: [entertainment, filter]
aliases: [Not Interested]
---
# \U0001f6ab Not Interested

Titles hidden from the briefing.

"""


def empty_titles_doc(kind: str) -> str:
    if kind == "watchlist":
        return WATCHLIST_HEADER
    if kind == "not_interested":
        return NOT_INTERESTED_HEADER
    raise ValueError(f"unknown kind: {kind}")


def iter_lines(text: str) -> Iterable[str]:
    return text.splitlines()

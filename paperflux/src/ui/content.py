"""Pure helpers behind the UI (no Streamlit imports, fully unit-testable)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

# Display order. "overview" stays the default view (no ?view= in the URL), see nav.py.
VIEWS = [("abstract", "Abstract"), ("overview", "Overview"), ("insights", "Insights"), ("analysis", "Analysis")]
VIEW_LABELS = {slug: label for slug, label in VIEWS}
LABEL_TO_SLUG = {label: slug for slug, label in VIEWS}

EXPLAINER, PENDING, ANALYSIS_ONLY, EMPTY = "explainer", "pending", "analysis", "empty"


# ------------------------------------------------------------------ papers --
def parse_published(paper: dict[str, Any]) -> Optional[datetime]:
    raw = paper.get("published_at")
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_date(paper: dict[str, Any]) -> str:
    published = parse_published(paper)
    return published.strftime("%b %d, %Y") if published else ""


def short_authors(authors: list[Any], limit: int = 3) -> str:
    names = [(a.get("name") if isinstance(a, dict) else str(a)) or "" for a in authors or []]
    names = [n for n in names if n]
    if not names:
        return ""
    shown = ", ".join(names[:limit])
    return shown + " et al." if len(names) > limit else shown


def sort_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most upvoted first. Storage order is just completion order, i.e. arbitrary."""
    def key(p: dict[str, Any]):
        published = parse_published(p)
        return (-(int(p.get("upvotes") or 0)), -(published.timestamp() if published else 0), p.get("title", ""))

    return sorted(papers, key=key)


def paper_status(paper: dict[str, Any]) -> str:
    insights = paper.get("insights") or {}
    if insights.get("schema_version") == 3 and insights.get("insights"):
        return EXPLAINER
    if not paper.get("explanation"):
        return EMPTY
    if insights.get("insights"):  # older stored format
        return ANALYSIS_ONLY
    return PENDING


def neighbors(papers: list[dict[str, Any]], paper_id: str) -> tuple[Optional[str], Optional[str], int, int]:
    ids = [p["paper_id"] for p in papers]
    if paper_id not in ids:
        return None, None, 0, len(ids)
    i = ids.index(paper_id)
    return (ids[i - 1] if i > 0 else None, ids[i + 1] if i < len(ids) - 1 else None, i + 1, len(ids))


def share_url(base: str, paper_id: str, view: Optional[str] = None) -> str:
    params = {"paper": paper_id}
    if view and view != "overview":
        params["view"] = view
    return f"{(base or '').rstrip('/')}/?{urlencode(params)}" if base else f"?{urlencode(params)}"


# ---------------------------------------------------------------- markdown --
def split_sections(markdown: str) -> list[tuple[str, str]]:
    """Split on level-2 headings (ignoring ``` fenced code). The leading '# Title' is dropped."""
    sections: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in (markdown or "").splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if match:
            sections.append((match.group(1).strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in sections if "\n".join(body).strip()]


_FIG_REF = re.compile(r"\b(?:figure|fig\.?)\s*(\d+)\s*([a-z])?\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z]{4,}")
_STOP = frozenset(
    "this that these those with from into over their paper figure shown using used than then "
    "each both only more most such also onto about when where what which into".split()
)
_RESULTS = re.compile(r"result|accurac|ablation|benchmark|compar", re.IGNORECASE)
_METHOD = re.compile(r"architect|overview|pipeline|method|model|diagram|system", re.IGNORECASE)


def figure_key(label: str) -> Optional[tuple[int, str]]:
    """(number, suffix) for 'Figure 3' or 'Fig. 1a'. None when the label is not a figure."""
    match = _FIG_REF.search(label or "")
    if not match:
        return None
    return int(match.group(1)), (match.group(2) or "").lower()


def weave_figures(sections: list[tuple[str, str]], figures: list[dict[str, Any]]) -> list[tuple[str, list]]:
    """Put each figure after the paragraph that first cites it.

    A figure the writeup never names is attached to the section whose words match its
    caption, or to How It Works / the experiments section when the caption says which
    kind of diagram it is. Each figure appears once.
    """
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for fig in figures or []:
        key = figure_key(str(fig.get("label") or ""))
        if key is not None and key not in by_key:
            by_key[key] = fig

    used: set[tuple[int, str]] = set()
    woven: list[tuple[str, list]] = []
    for title, body in sections:
        blocks: list = []
        body_keys = set(_figure_refs(body))
        for key in _figure_refs(title):
            if key in by_key and key not in used and key not in body_keys:
                blocks.append(by_key[key])
                used.add(key)
        for paragraph in _paragraphs(body):
            blocks.append(paragraph)
            for key in _figure_refs(paragraph):
                if key in by_key and key not in used:
                    blocks.append(by_key[key])
                    used.add(key)
        woven.append((title, blocks))

    leftover = [fig for key, fig in by_key.items() if key not in used]
    for fig in sorted(leftover, key=lambda item: figure_key(str(item.get("label") or "")) or (0, "")):
        if woven:
            woven[_best_section(woven, fig)][1].append(fig)
    return woven


def _paragraphs(body: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", (body or "").strip()) if part.strip()]


def _figure_refs(text: str) -> list[tuple[int, str]]:
    return [(int(match.group(1)), (match.group(2) or "").lower()) for match in _FIG_REF.finditer(text or "")]


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall((text or "").lower()) if word not in _STOP}


def _best_section(woven: list[tuple[str, list]], fig: dict[str, Any]) -> int:
    caption = str(fig.get("caption") or "")
    wanted = _words(caption)
    best_index, best_score = 0, 0
    for index, (title, blocks) in enumerate(woven):
        text = title + " " + " ".join(block for block in blocks if isinstance(block, str))
        score = len(wanted & _words(text))
        if score > best_score:
            best_index, best_score = index, score
    if best_score >= 2:
        return best_index
    titles = [title for title, _ in woven]
    preferred = _RESULTS if _RESULTS.search(caption) else _METHOD
    heading = re.compile(r"experiment", re.I) if preferred is _RESULTS else re.compile(r"how it works", re.I)
    for index, title in enumerate(titles):
        if heading.search(title):
            return index
    for index, title in enumerate(titles):
        if not re.search(r"glossary|could build|skeptic", title, re.I):
            best_index = index
    return best_index


def find_section(markdown: str, name: str) -> str:
    wanted = name.lower()
    for title, body in split_sections(markdown):
        if wanted in title.lower():
            return body
    return ""


def reading_minutes(text: str) -> int:
    return max(1, round(len((text or "").split()) / 220))


_MD_NOISE = re.compile(r"[*_`>#]|\[(.*?)\]\(.*?\)")


def _plain(text: str) -> str:
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_`>#$]", "", text)
    return " ".join(text.split())


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.- ")
    return cut + "…"


def blurb(paper: dict[str, Any], limit: int = 240) -> str:
    """Best short description available: TL;DR, else the analysis TL;DR, else the abstract."""
    tldr = (paper.get("insights") or {}).get("tldr")
    if tldr:
        return clip(_plain(tldr), limit)
    from_analysis = find_section(paper.get("explanation") or "", "tl;dr")
    if from_analysis:
        return clip(_plain(from_analysis), limit)
    return clip(_plain(paper.get("summary") or ""), limit)

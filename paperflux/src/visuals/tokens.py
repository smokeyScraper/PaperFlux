"""Token-sequence animations: what a method does to the tokens themselves.

Speculative decoding (draft, accept, reject), masked diffusion (unmask step by step),
pruning or eviction (drop), routing (tag each token with its expert), attention (focus).
The model supplies tokens and per-step state changes; layout, colours and animation are
deterministic, and all text is escaped, exactly as for flows.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from PIL import Image, ImageDraw

from paperflux.src.visuals import raster
from paperflux.src.visuals.flow import (
    ACCENT,
    CARD_FILL,
    CARD_STROKE,
    HOLD_SECONDS,
    PAD,
    STEP_SECONDS,
    TEXT_DARK,
    TEXT_MUTED,
    _clip,
    _esc,
    _keyframes,
    _text_w,
)

STATES = ("plain", "new", "keep", "drop", "mask", "focus", "hidden")
# state -> (fill, stroke, text colour)
STATE_STYLE: dict[str, tuple[str, str, str]] = {
    "plain": ("#f1f5f9", "#cbd5e1", TEXT_DARK),
    "new": ("#ede9fe", ACCENT, TEXT_DARK),
    "keep": ("#dcfce7", "#16a34a", TEXT_DARK),
    "drop": ("#fee2e2", "#dc2626", "#991b1b"),
    "mask": ("#e2e8f0", "#94a3b8", TEXT_MUTED),
    "focus": ("#fef3c7", "#d97706", TEXT_DARK),
    "hidden": (CARD_FILL, "#d5dce6", TEXT_MUTED),
}
MAX_TOKENS = 24
MAX_STEPS = 6
MAX_TOKEN = 16
MAX_TAG = 8
MAX_MEANING = 22

W = 720
HEADER = 62
FOOTER = 58
CELL_H = 38
CELL_MIN_W = 40
GAP_X = 8
ROW_GAP = 14
TAG_SPACE = 17
LEGEND_H = 30
TOKEN_SIZE = 15
TAG_SIZE = 10.5
INTRO_SECONDS = 2.0
FADE = 0.3

INTRO_MS = 1600
BLEND_FRAMES = 3
BLEND_MS = 70
STEP_HOLD_MS = 2200
FINAL_HOLD_MS = 3000


@dataclass(frozen=True)
class Look:
    state: str
    text: str
    tag: str = ""

    def shown(self) -> str:
        """What the cell displays: masked tokens hide their text, hidden ones show nothing."""
        return "?" if self.state == "mask" else ("" if self.state == "hidden" else self.text)


@dataclass
class Change:
    index: int
    state: str
    text: str = ""
    tag: Optional[str] = None  # None keeps the token's current tag

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"index": self.index, "state": self.state, "text": self.text}
        if self.tag is not None:
            out["tag"] = self.tag
        return out


@dataclass
class TokStep:
    caption: str
    changes: list[Change]


def _state(value: Any, default: str) -> str:
    state = str(value or "").strip().lower()
    return state if state in STATES else default


def _index(value: Any, size: int) -> Optional[int]:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < size else None


@dataclass
class TokenSpec:
    title: str
    caption: str
    tokens: list[Look]
    steps: list[TokStep]
    legend: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> Optional["TokenSpec"]:
        """Tolerant parser for model output. Returns None if unusable."""
        if not isinstance(raw, dict):
            return None
        warnings: list[str] = []
        tokens: list[Look] = []
        for item in raw.get("tokens") or []:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict):
                continue
            text = _clip(item.get("text"), MAX_TOKEN)
            if not text:
                continue
            if len(tokens) >= MAX_TOKENS:
                warnings.append(f"Dropped tokens beyond the first {MAX_TOKENS}")
                break
            tokens.append(Look(_state(item.get("state"), "plain"), text, _clip(item.get("tag"), MAX_TAG)))
        if len(tokens) < 2:
            return None

        steps: list[TokStep] = []
        for item in raw.get("steps") or []:
            if not isinstance(item, dict):
                continue
            caption = _clip(item.get("caption") or item.get("text"), 84)
            changes: list[Change] = []
            for change in item.get("changes") or []:
                if not isinstance(change, dict):
                    continue
                refs = change.get("indices") if isinstance(change.get("indices"), list) else [change.get("index")]
                for ref in refs:
                    index = _index(ref, len(tokens))
                    if index is None:
                        warnings.append(f"Ignored a change to token {ref!r} (out of range)")
                        continue
                    tag = change.get("tag")
                    changes.append(Change(index, _state(change.get("state"), "plain"),
                                          _clip(change.get("text"), MAX_TOKEN),
                                          None if tag is None else _clip(tag, MAX_TAG)))
            if caption and changes:
                steps.append(TokStep(caption, changes))
            if len(steps) >= MAX_STEPS:
                break
        if not steps:
            return None

        legend_raw = raw.get("legend") if isinstance(raw.get("legend"), dict) else {}
        legend = {s: _clip(legend_raw.get(s), MAX_MEANING) for s in STATES if legend_raw.get(s)}
        return cls(_clip(raw.get("title"), 70), _clip(raw.get("caption"), 110), tokens, steps, legend, warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tokens",
            "title": self.title,
            "caption": self.caption,
            "tokens": [{"text": t.text, "state": t.state, "tag": t.tag} for t in self.tokens],
            "steps": [{"caption": s.caption, "changes": [c.to_dict() for c in s.changes]} for s in self.steps],
            "legend": dict(self.legend),
        }

    def fingerprint(self) -> str:
        return hashlib.sha1(json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def slots(self) -> list[list[Look]]:
        """Token looks per slot: slot 0 is the starting sequence, slot k+1 follows step k."""
        current = list(self.tokens)
        out = [list(current)]
        for step in self.steps:
            for c in step.changes:
                old = current[c.index]
                current[c.index] = Look(c.state, c.text or old.text, old.tag if c.tag is None else c.tag)
            out.append(list(current))
        return out


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
@dataclass
class TokenLayout:
    spec: TokenSpec
    width: float
    height: float
    cells: list[tuple[float, float, float, float]]
    slots: list[list[Look]]
    legend: list[tuple[str, str, float]]  # (state, meaning, x)
    legend_y: float
    total_seconds: float

    def slot_start(self, slot: int) -> float:
        return 0.0 if slot == 0 else INTRO_SECONDS + (slot - 1) * STEP_SECONDS

    def phases(self, index: int) -> list[tuple[int, int, Look]]:
        """(first slot, end slot exclusive, look) runs of an unchanged look for one token."""
        runs: list[tuple[int, int, Look]] = []
        for slot, looks in enumerate(self.slots):
            look = looks[index]
            if runs and runs[-1][2] == look:
                runs[-1] = (runs[-1][0], slot + 1, look)
            else:
                runs.append((slot, slot + 1, look))
        return runs


def layout_tokens(spec: TokenSpec) -> TokenLayout:
    slots = spec.slots()
    widths = [
        max(CELL_MIN_W, max(_text_w(s[i].shown(), TOKEN_SIZE) for s in slots) + 22)
        for i in range(len(spec.tokens))
    ]
    tagged = any(look.tag for s in slots for look in s)
    row_h = CELL_H + (TAG_SPACE if tagged else 0)
    inner = W - 2 * PAD
    rows: list[list[int]] = [[]]
    used = 0.0
    for i, w in enumerate(widths):
        if rows[-1] and used + GAP_X + w > inner:
            rows.append([])
            used = 0.0
        used += (GAP_X if rows[-1] else 0) + w
        rows[-1].append(i)
    block_w = max(sum(widths[i] for i in row) + GAP_X * (len(row) - 1) for row in rows)
    x0 = (W - block_w) / 2
    cells: list[tuple[float, float, float, float]] = [(0, 0, 0, 0)] * len(widths)
    y = HEADER + 14
    for row in rows:
        x = x0
        for i in row:
            cells[i] = (x, y, widths[i], CELL_H)
            x += widths[i] + GAP_X
        y += row_h + ROW_GAP
    y -= ROW_GAP

    legend: list[tuple[str, str, float]] = []
    legend_y = y + 18
    if spec.legend:
        x = float(PAD)
        for state, meaning in spec.legend.items():
            legend.append((state, meaning, x))
            x += 22 + _text_w(meaning, 11) + 22
        y += LEGEND_H
    total = INTRO_SECONDS + len(spec.steps) * STEP_SECONDS + HOLD_SECONDS
    return TokenLayout(spec, W, y + 12 + FOOTER, cells, slots, legend, legend_y, total)


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------
def _interval_css(layout: TokenLayout, a: int, b: int) -> str:
    total, n_slots = layout.total_seconds, len(layout.slots)
    pct = lambda seconds: seconds * 100.0 / total  # noqa: E731
    end_hold = total * 0.97
    start, end = layout.slot_start(a), layout.slot_start(b) if b < n_slots else None
    stops: list[tuple[float, str]] = [(0, "opacity:1" if a == 0 else "opacity:0")]
    if a > 0:
        stops += [(pct(start), "opacity:0"), (pct(start + FADE), "opacity:1")]
    if end is None:
        stops += [(pct(end_hold), "opacity:1"), (100, "opacity:0")]
    else:
        stops += [(pct(end), "opacity:1"), (pct(end + FADE), "opacity:0")]
        stops += [(pct(end_hold), "opacity:0"), (100, "opacity:1")] if a == 0 else [(100, "opacity:0")]
    return _keyframes(f"iv{a}_{b}", stops)


def _cell_svg(x: float, y: float, w: float, h: float, look: Look) -> str:
    fill, stroke, color = STATE_STYLE[look.state]
    dash = ' stroke-dasharray="4 3"' if look.state in {"mask", "hidden"} else ""
    weight = ' font-weight="600"' if look.state in {"new", "keep", "focus"} else ""
    cx, shown = x + w / 2, look.shown()
    parts = [(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="8" fill="{fill}" '
              f'stroke="{stroke}" stroke-width="1.6"{dash}/>')]
    if shown:
        parts.append(f'<text x="{cx:.1f}" y="{y + h / 2 + 5:.1f}" font-size="{TOKEN_SIZE}" text-anchor="middle" '
                     f'fill="{color}"{weight}>{_esc(shown)}</text>')
    if look.state == "drop" and shown:
        half = _text_w(shown, TOKEN_SIZE) / 2 + 4
        parts.append(f'<line x1="{cx - half:.1f}" y1="{y + h / 2:.1f}" x2="{cx + half:.1f}" y2="{y + h / 2:.1f}" '
                     f'stroke="{stroke}" stroke-width="1.8"/>')
    if look.tag:
        parts.append(f'<text x="{cx:.1f}" y="{y + h + 13:.1f}" font-size="{TAG_SIZE}" text-anchor="middle" '
                     f'font-weight="600" fill="{TEXT_MUTED}">{_esc(look.tag)}</text>')
    return "".join(parts)


def render_tokens_svg(layout: TokenLayout) -> str:
    spec, n_slots = layout.spec, len(layout.slots)
    W_, H = layout.width, layout.height
    intervals: set[tuple[int, int]] = set()
    body: list[str] = []
    for i, (x, y, w, h) in enumerate(layout.cells):
        for a, b, look in layout.phases(i):
            static = a == 0 and b == n_slots
            if not static:
                intervals.add((a, b))
            cls = "tk fin" if static else f"tk p{a}_{b}" + (" fin" if b == n_slots else "")
            body.append(f'<g class="{cls}">{_cell_svg(x, y, w, h, look)}</g>')
    captions: list[str] = []
    for k, step in enumerate(spec.steps):
        a, b = k + 1, k + 2
        intervals.add((a, b))
        cls = f"cp p{a}_{b}" + (" fin" if b == n_slots else "")
        captions.append(
            f'<text class="{cls}" x="{W_ / 2:.0f}" y="{H - 24}" font-size="14" text-anchor="middle" fill="{TEXT_DARK}">'
            f'<tspan fill="{ACCENT}" font-weight="700">{k + 1}/{len(spec.steps)}  </tspan>{_esc(step.caption)}</text>'
        )
    css = [
        "text{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}",
        ".tk,.cp{opacity:0}.tk.fin,.cp.fin{opacity:1}",  # reduced motion: the end state
    ]
    css += [_interval_css(layout, a, b) for a, b in sorted(intervals)]
    css.append("@media (prefers-reduced-motion:no-preference){" + "".join(
        f".p{a}_{b}{{animation:iv{a}_{b} {layout.total_seconds:.2f}s linear infinite}}"
        for a, b in sorted(intervals)) + "}")
    desc = " ".join(f"{i + 1}. {s.caption}" for i, s in enumerate(spec.steps))
    label = _esc(spec.title or "Token animation")
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W_} {H}" role="img" aria-label="{label}" '
         f'style="width:100%;max-width:{W_}px;height:auto;display:block;margin:0 auto">'),
        f"<title>{label}</title><desc>{_esc(desc)}</desc>",
        f"<style>{''.join(css)}</style>",
        f'<rect width="{W_}" height="{H}" rx="14" fill="{CARD_FILL}" stroke="{CARD_STROKE}"/>',
    ]
    if spec.title:
        out.append(f'<text x="{PAD}" y="34" font-size="17" font-weight="700" fill="{TEXT_DARK}">{label}</text>')
    if spec.caption:
        out.append(f'<text x="{PAD}" y="53" font-size="12" fill="{TEXT_MUTED}">{_esc(spec.caption)}</text>')
    out += body
    for state, meaning, x in layout.legend:
        fill, stroke, _ = STATE_STYLE[state]
        y = layout.legend_y
        out.append(f'<rect x="{x:.1f}" y="{y - 11:.1f}" width="14" height="14" rx="4" fill="{fill}" stroke="{stroke}" '
                   f'stroke-width="1.4"/><text x="{x + 20:.1f}" y="{y:.1f}" font-size="11" fill="{TEXT_MUTED}">'
                   f"{_esc(meaning)}</text>")
    out += captions
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------
# GIF
# --------------------------------------------------------------------------
def draw_slot(layout: TokenLayout, slot: int) -> Image.Image:
    spec = layout.spec
    S = raster.SCALE
    img, d = raster.canvas(layout.width, layout.height, CARD_FILL, CARD_STROKE)
    if spec.title:
        raster.text(d, (PAD, 34), spec.title, 17, raster.rgb(TEXT_DARK), anchor="ls", bold=True)
    if spec.caption:
        raster.text(d, (PAD, 53), spec.caption, 12, raster.rgb(TEXT_MUTED), anchor="ls")
    for (x, y, w, h), look in zip(layout.cells, layout.slots[slot]):
        _draw_cell(d, x, y, w, h, look)
    for state, meaning, x in layout.legend:
        fill, stroke, _ = STATE_STYLE[state]
        y = layout.legend_y
        d.rounded_rectangle((x * S, (y - 11) * S, (x + 14) * S, (y + 3) * S), radius=4 * S,
                            fill=raster.rgb(fill), outline=raster.rgb(stroke), width=S)
        raster.text(d, (x + 20, y), meaning, 11, raster.rgb(TEXT_MUTED), anchor="ls")
    if slot > 0:
        _draw_caption(d, layout, slot - 1)
    return raster.finish(img, layout.width, layout.height)


def _draw_cell(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, look: Look) -> None:
    S = raster.SCALE
    fill, stroke, color = STATE_STYLE[look.state]
    d.rounded_rectangle((x * S, y * S, (x + w) * S, (y + h) * S), radius=8 * S,
                        fill=raster.rgb(fill), outline=raster.rgb(stroke), width=max(1, int(1.6 * S)))
    cx, shown = x + w / 2, look.shown()
    if shown:
        raster.text(d, (cx, y + h / 2 + 5), shown, TOKEN_SIZE, raster.rgb(color),
                    bold=look.state in {"new", "keep", "focus"})
    if look.state == "drop" and shown:
        half = raster.text_width(shown, TOKEN_SIZE) / 2 + 4
        d.line(((cx - half) * S, (y + h / 2) * S, (cx + half) * S, (y + h / 2) * S),
               fill=raster.rgb(stroke), width=int(1.8 * S))
    if look.tag:
        raster.text(d, (cx, y + h + 13), look.tag, TAG_SIZE, raster.rgb(TEXT_MUTED), bold=True)


def _draw_caption(d: ImageDraw.ImageDraw, layout: TokenLayout, k: int) -> None:
    prefix = f"{k + 1}/{len(layout.spec.steps)}  "
    caption = layout.spec.steps[k].caption
    total = raster.text_width(prefix + caption, 14)
    x0, y = (layout.width - total) / 2, layout.height - 24
    raster.text(d, (x0, y), prefix, 14, raster.rgb(ACCENT), anchor="ls")
    raster.text(d, (x0 + raster.text_width(prefix, 14), y), caption, 14, raster.rgb(TEXT_DARK), anchor="ls")


def render_tokens_gif(layout: TokenLayout) -> bytes:
    frames = [draw_slot(layout, 0)]
    durations = [INTRO_MS]
    last = len(layout.slots) - 1
    for slot in range(1, last + 1):
        before, after = frames[-1], draw_slot(layout, slot)
        for i in range(1, BLEND_FRAMES + 1):
            frames.append(Image.blend(before, after, i / (BLEND_FRAMES + 1)))
            durations.append(BLEND_MS)
        frames.append(after)
        durations.append(FINAL_HOLD_MS if slot == last else STEP_HOLD_MS)
    return raster.encode_gif(frames, durations)

"""Animated GIF of a results chart: baselines appear first, then the paper's method, then a
callout with the gap to the best baseline. Same data, colours and zero baseline as the
inline Vega-Lite chart, so the GIF cannot exaggerate a difference.
"""
from __future__ import annotations

import math
from typing import Optional

from PIL import Image, ImageDraw

from paperflux.src.visuals import raster
from paperflux.src.visuals.chart import BASELINE_COLORS, HIGHLIGHT_COLOR, ChartSpec
from paperflux.src.visuals.flow import CARD_FILL, CARD_STROKE, PAD, TEXT_DARK, TEXT_MUTED, _clip, wrap_text

PALETTE = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#ef4444"]  # config.toml chartCategoricalColors
GRID = "#e2e8f0"
W, H = 720, 460
PLOT_LEFT, PLOT_RIGHT, PLOT_TOP = 70, W - 28, 76
PLOT_BOTTOM = H - 118
GROW_FRAMES = 10
GROW_MS = 45
PHASE_HOLD_MS = 900
FINAL_HOLD_MS = 3200


def direction(y_label: str) -> Optional[str]:
    """"higher" / "lower" when the label says which way is better (our prompt asks for it)."""
    text = (y_label or "").lower()
    if "lower is better" in text or "lower = better" in text or "↓" in text:
        return "lower"
    if "higher is better" in text or "higher = better" in text or "↑" in text:
        return "higher"
    return None


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def callout(chart: ChartSpec) -> str:
    """'Ours vs best baseline: A +1.2, B -0.3' for bar/line charts with a highlighted method."""
    better = direction(chart.y_label)
    if not chart.highlight or better is None or chart.kind == "scatter" or len(chart.series) < 2:
        return ""
    ours = dict((p.label, p.value) for name, pts in chart.series if name == chart.highlight for p in pts)
    parts: list[str] = []
    for label, value in ours.items():
        rivals = [p.value for name, pts in chart.series if name != chart.highlight for p in pts if p.label == label]
        if rivals:
            best = max(rivals) if better == "higher" else min(rivals)
            parts.append(f"{label} {value - best:+.3g}")
    if not parts:
        return ""
    return _clip(f"{chart.highlight} vs best baseline: " + ", ".join(parts[:4]), 92)


def _colors(chart: ChartSpec) -> dict[str, str]:
    names = [name for name, _ in chart.series]
    if chart.highlight:
        greys = iter(BASELINE_COLORS * 2)
        return {n: HIGHLIGHT_COLOR if n == chart.highlight else next(greys) for n in names}
    return {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + (abs(lo) or 1.0)
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start, end = math.floor(lo / step) * step, math.ceil(hi / step) * step
    ticks, value = [], start
    while value <= end + step * 1e-6:
        ticks.append(round(value, 10))
        value += step
    return ticks


class _Axis:
    """Maps data values to pixels along one axis (linear or log10)."""

    def __init__(self, lo: float, hi: float, p0: float, p1: float, log: bool = False):
        self.log, self.p0, self.p1 = log, p0, p1
        self.lo, self.hi = (math.log10(lo), math.log10(hi)) if log else (lo, hi)

    def __call__(self, value: float) -> float:
        v = math.log10(value) if self.log else value
        return self.p0 + (v - self.lo) / ((self.hi - self.lo) or 1.0) * (self.p1 - self.p0)


def _phases(chart: ChartSpec) -> list[list[str]]:
    """Series revealed together: baselines first, then the paper's method last."""
    names = [name for name, _ in chart.series]
    if chart.highlight and len(names) > 1:
        return [[n for n in names if n != chart.highlight], [chart.highlight]]
    return [names]


class ChartFrames:
    def __init__(self, chart: ChartSpec):
        self.chart = chart
        self.colors = _colors(chart)
        self.note = callout(chart) or (f"Source: {chart.source}" if chart.source else "")
        values = [p.value for _, pts in chart.series for p in pts]
        if chart.kind == "scatter":
            self.y_ticks = nice_ticks(min(values), max(values))
            xs = [p.x for _, pts in chart.series for p in pts if p.x is not None]
            log = min(xs) > 0 and max(xs) / min(xs) >= 100
            if log:
                lo, hi = math.floor(math.log10(min(xs))), math.ceil(math.log10(max(xs)))
                self.x_ticks = [10.0 ** e for e in range(lo, hi + 1)]
            else:
                self.x_ticks = nice_ticks(min(xs), max(xs))
            self.x = _Axis(self.x_ticks[0], self.x_ticks[-1], PLOT_LEFT + 14, PLOT_RIGHT - 70, log)
        else:
            self.y_ticks = nice_ticks(min(0.0, min(values)), max(0.0, max(values)))
            self.labels = list(dict.fromkeys(p.label for _, pts in chart.series for p in pts))
            self.band = (PLOT_RIGHT - PLOT_LEFT) / len(self.labels)
        self.y = _Axis(self.y_ticks[0], self.y_ticks[-1], PLOT_BOTTOM, PLOT_TOP)

    # ---- frame -----------------------------------------------------------
    def draw(self, progress: dict[str, float], show_note: bool) -> Image.Image:
        S, chart = raster.SCALE, self.chart
        img, d = raster.canvas(W, H, CARD_FILL, CARD_STROKE)
        if chart.title:
            raster.text(d, (PAD, 34), chart.title, 17, raster.rgb(TEXT_DARK), anchor="ls", bold=True)
        if chart.y_label:
            raster.text(d, (PAD, 53), chart.y_label, 12, raster.rgb(TEXT_MUTED), anchor="ls")
        for tick in self.y_ticks:
            y = self.y(tick)
            d.line((PLOT_LEFT * S, y * S, PLOT_RIGHT * S, y * S), fill=raster.rgb(GRID), width=S)
            raster.text(d, (PLOT_LEFT - 8, y + 4), _fmt(tick), 11, raster.rgb(TEXT_MUTED), anchor="rs")
        if chart.kind == "scatter":
            self._scatter(d, progress)
        else:
            for i, label in enumerate(self.labels):
                cx = PLOT_LEFT + self.band * (i + 0.5)
                for j, line in enumerate(wrap_text(label, 11, self.band - 6, False, 2)):
                    raster.text(d, (cx, PLOT_BOTTOM + 18 + 14 * j), line, 11, raster.rgb(TEXT_MUTED))
            (self._line if chart.kind == "line" else self._bars)(d, progress)
        self._legend(d)
        if show_note and self.note:
            raster.text(d, (W / 2, H - 22), self.note, 13.5, raster.rgb(TEXT_DARK), bold=bool(callout(chart)))
        return raster.finish(img, W, H)

    def _bars(self, d: ImageDraw.ImageDraw, progress: dict[str, float]) -> None:
        S, names = raster.SCALE, [n for n, _ in self.chart.series]
        group_w = self.band * 0.78
        bar_w = group_w / len(names)
        zero = self.y(0.0)  # the ticks always span 0 for bars
        for k, (name, points) in enumerate(self.chart.series):
            t = raster.ease_out(progress.get(name, 0.0))
            if t <= 0:
                continue
            for p in points:
                i = self.labels.index(p.label)
                x0 = PLOT_LEFT + self.band * i + (self.band - group_w) / 2 + bar_w * k + 1
                top = zero + (self.y(p.value) - zero) * t
                d.rounded_rectangle((x0 * S, min(top, zero) * S, (x0 + bar_w - 2) * S, max(top, zero) * S),
                                    radius=3 * S, fill=raster.rgb(self.colors[name]))
                if t >= 0.999:
                    raster.text(d, (x0 + bar_w / 2 - 1, min(top, zero) - 6), _fmt(p.value), 11,
                                raster.rgb(TEXT_DARK), bold=name == self.chart.highlight)

    def _line(self, d: ImageDraw.ImageDraw, progress: dict[str, float]) -> None:
        S = raster.SCALE
        for name, points in self.chart.series:
            t = progress.get(name, 0.0)
            if t <= 0:
                continue
            ordered = sorted(points, key=lambda p: self.labels.index(p.label))
            pts = [(PLOT_LEFT + self.band * (self.labels.index(p.label) + 0.5), self.y(p.value)) for p in ordered]
            color = raster.rgb(self.colors[name])
            shown = raster.partial(pts, t) if len(pts) > 1 else pts
            if len(shown) > 1:
                d.line([(x * S, y * S) for x, y in shown], fill=color, width=3 * S, joint="curve")
            for (x, y), p in zip(pts, ordered):
                if x <= shown[-1][0] + 0.5:
                    d.ellipse(((x - 4) * S, (y - 4) * S, (x + 4) * S, (y + 4) * S), fill=color)
                    if t >= 0.999 and name == self.chart.highlight:
                        raster.text(d, (x, y - 10), _fmt(p.value), 11, raster.rgb(TEXT_DARK), bold=True)

    def _scatter(self, d: ImageDraw.ImageDraw, progress: dict[str, float]) -> None:
        S = raster.SCALE
        for tick in self.x_ticks:
            x = self.x(tick)
            d.line((x * S, PLOT_TOP * S, x * S, PLOT_BOTTOM * S), fill=raster.rgb(GRID), width=S)
            raster.text(d, (x, PLOT_BOTTOM + 18), _fmt(tick), 11, raster.rgb(TEXT_MUTED))
        if self.chart.x_label:
            raster.text(d, ((PLOT_LEFT + PLOT_RIGHT) / 2, PLOT_BOTTOM + 38), self.chart.x_label, 12,
                        raster.rgb(TEXT_MUTED))
        for name, points in self.chart.series:
            t = raster.ease_out(progress.get(name, 0.0))
            if t <= 0:
                continue
            for p in points:
                x, y, r = self.x(p.x), self.y(p.value), 7 * t
                d.ellipse(((x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S), fill=raster.rgb(self.colors[name]),
                          outline=(255, 255, 255), width=S)
                if t >= 0.999:
                    raster.text(d, (x + 11, y - 7), p.label, 11, raster.rgb(TEXT_DARK), anchor="ls",
                                bold=name == self.chart.highlight)

    def _legend(self, d: ImageDraw.ImageDraw) -> None:
        if len(self.chart.series) < 2:
            return
        S, x, y = raster.SCALE, float(PAD), H - 56
        for name, _ in self.chart.series:
            d.rounded_rectangle((x * S, (y - 10) * S, (x + 12) * S, (y + 2) * S), radius=3 * S,
                                fill=raster.rgb(self.colors[name]))
            raster.text(d, (x + 18, y), name, 11.5, raster.rgb(TEXT_DARK), anchor="ls",
                        bold=name == self.chart.highlight)
            x += 18 + raster.text_width(name, 11.5) + 22


def render_chart_gif(chart: ChartSpec) -> bytes:
    frames_ = ChartFrames(chart)
    phases = _phases(chart)
    done: dict[str, float] = {}
    frames = [frames_.draw(done, show_note=False)]
    durations = [500]
    for k, group in enumerate(phases):
        for i in range(1, GROW_FRAMES + 1):
            frames.append(frames_.draw({**done, **{n: i / GROW_FRAMES for n in group}}, show_note=False))
            durations.append(GROW_MS)
        done.update({n: 1.0 for n in group})
        durations[-1] = PHASE_HOLD_MS if k < len(phases) - 1 else PHASE_HOLD_MS // 2
    frames.append(frames_.draw(done, show_note=True))
    durations.append(FINAL_HOLD_MS)
    return raster.encode_gif(frames, durations)


def chart_to_gif(raw_or_spec) -> Optional[bytes]:
    chart = raw_or_spec if isinstance(raw_or_spec, ChartSpec) else ChartSpec.from_raw(raw_or_spec)
    return render_chart_gif(chart) if chart else None

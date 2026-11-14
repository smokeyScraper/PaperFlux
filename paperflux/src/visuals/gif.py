"""Export a flow diagram as a looping GIF (shareable "explainer" animation).

Uses the same ``Layout`` as the inline animated SVG, drawn frame-by-frame with
Pillow (no browser, no system dependencies). Supersampled 2x for smooth edges.
"""
from __future__ import annotations

import io
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from paperflux.src.visuals.flow import (
    ACCENT,
    BACK_EDGE_COLOR,
    CARD_FILL,
    CARD_STROKE,
    DETAIL_SIZE,
    EDGE_COLOR,
    KIND_COLORS,
    LABEL_SIZE,
    PAD,
    TEXT_DARK,
    TEXT_MUTED,
    EdgeGeom,
    FlowSpec,
    Layout,
    layout_flow,
)

SCALE = 2
DRAW_FRAMES = 6
DRAW_FRAME_MS = 70
HOLD_MS = 1100
FINAL_HOLD_MS = 2600
MAX_WIDTH = 900
DIM = 0.22


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(fg: str, bg: str, alpha: float) -> tuple[int, int, int]:
    a = max(0.0, min(1.0, alpha))
    f, b = _rgb(fg), _rgb(bg)
    return tuple(int(round(b[i] + (f[i] - b[i]) * a)) for i in range(3))  # type: ignore[return-value]


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


_ASCII_FALLBACK = {
    "×": "x", "·": ".", "…": "...", "–": "-", "—": "-", "−": "-", "’": "'", "‘": "'",
    "“": '"', "”": '"', "≥": ">=", "≤": "<=", "→": "->", "←": "<-", "↔": "<->",
    "≈": "~", "≠": "!=", "±": "+/-", "∞": "inf", "°": "deg", "µ": "u", "μ": "u",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "θ": "theta", "λ": "lambda",
    "σ": "sigma", "π": "pi", "ε": "eps", "∑": "sum", "∏": "prod", "√": "sqrt",
    "\u00a0": " ", "\u2009": " ", "\u200b": "",
}


def _glyph_bytes(font: ImageFont.FreeTypeFont, ch: str, size: int) -> bytes:
    canvas = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(canvas).text((2, 2), ch, font=font, fill=255)
    return canvas.tobytes()


@lru_cache(maxsize=4096)
def _glyph_ok(size: int, ch: str) -> bool:
    if ch.isspace():
        return True
    font = _font(size)
    # U+FFFE is a non-character, so it renders as the font's .notdef (tofu) glyph.
    return _glyph_bytes(font, ch, size) != _glyph_bytes(font, "\ufffe", size)


def _drawable(text: str, size: int) -> str:
    """Map symbols the bundled font lacks to ASCII (avoids tofu boxes in the GIF)."""
    out: list[str] = []
    for ch in text:
        if _glyph_ok(size, ch):
            out.append(ch)
        else:
            out.append(_ASCII_FALLBACK.get(ch, ""))
    return "".join(out)


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    size: float,
    fill: tuple[int, int, int],
    anchor: str = "ms",
    bold: bool = False,
) -> None:
    px = max(8, int(round(size * SCALE)))
    font = _font(px)
    text = _drawable(text, px)
    x, y = xy[0] * SCALE, xy[1] * SCALE
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)
    if bold:  # the bundled font has a single weight: fake bold with a 1px smear
        draw.text((x + 1, y), text, font=font, fill=fill, anchor=anchor)


def _partial(points: list[tuple[float, float]], fraction: float) -> list[tuple[float, float]]:
    if fraction >= 1.0:
        return points
    lengths = [
        ((points[i + 1][0] - points[i][0]) ** 2 + (points[i + 1][1] - points[i][1]) ** 2) ** 0.5
        for i in range(len(points) - 1)
    ]
    target = sum(lengths) * max(0.0, fraction)
    out = [points[0]]
    run = 0.0
    for i, seg in enumerate(lengths):
        if run + seg >= target:
            t = 0.0 if seg == 0 else (target - run) / seg
            x = points[i][0] + (points[i + 1][0] - points[i][0]) * t
            y = points[i][1] + (points[i + 1][1] - points[i][1]) * t
            out.append((x, y))
            return out
        run += seg
        out.append(points[i + 1])
    return out


def draw_frame(layout: Layout, step: int, progress: float) -> Image.Image:
    """Render the diagram with steps < ``step`` complete and ``step`` at ``progress``."""
    spec = layout.spec
    W, H = int(layout.width), int(layout.height)
    img = Image.new("RGB", (W * SCALE, H * SCALE), _rgb(CARD_FILL))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (1, 1, W * SCALE - 2, H * SCALE - 2),
        radius=14 * SCALE,
        outline=_rgb(CARD_STROKE),
        width=SCALE,
    )
    if spec.title:
        _text(d, (PAD, 34), spec.title, 17, _rgb(TEXT_DARK), anchor="ls", bold=True)
    if spec.caption:
        _text(d, (PAD, 53), spec.caption, 12, _rgb(TEXT_MUTED), anchor="ls")

    ox, oy = layout.ox, layout.oy

    def P(x: float, y: float) -> tuple[float, float]:
        return ((x + ox) * SCALE, (y + oy) * SCALE)

    for name, gx, gy, gw, gh in layout.groups:
        d.rounded_rectangle(
            (*P(gx, gy), *P(gx + gw, gy + gh)),
            radius=12 * SCALE,
            fill=_mix("#e2e8f0", CARD_FILL, 0.6),
            outline=_rgb("#cbd5e1"),
            width=SCALE,
        )
        _text(d, (gx + ox + 10, gy + oy + 16), name, 11, _rgb(TEXT_MUTED), anchor="ls", bold=True)

    def edge_fraction(g: EdgeGeom) -> float:
        if g.step < step:
            return 1.0
        if g.step == step:
            return progress
        return 0.0

    for g in layout.edges:
        frac = edge_fraction(g)
        if frac <= 0:
            continue
        color = _rgb(BACK_EDGE_COLOR if g.back else EDGE_COLOR)
        pts = [P(x, y) for x, y in _partial(g.polyline(14), frac)]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=2 * SCALE, joint="curve")
        if frac >= 0.999:
            d.polygon([P(x, y) for x, y in g.arrow], fill=color)
            if g.edge.label:
                lx, ly = g.label_pos
                font = _font(int(11 * SCALE))
                tw = d.textlength(_drawable(g.edge.label, int(11 * SCALE)), font=font)
                cx, cy = P(lx, ly)
                d.rectangle(
                    (cx - tw / 2 - 3, cy - 12 * SCALE + 2, cx + tw / 2 + 3, cy + 3 * SCALE),
                    fill=_rgb(CARD_FILL),
                )
                _text(d, (lx + ox, ly + oy), g.edge.label, 11, _rgb(TEXT_MUTED))

    for p in layout.placed.values():
        s = layout.node_step[p.node.id]
        alpha = 1.0 if s < step else (DIM + (1 - DIM) * progress if s == step else DIM)
        fill, stroke = KIND_COLORS[p.node.kind]
        width = (4 - 2.4 * progress) if (s == step and progress < 1.0) else 1.6
        d.rounded_rectangle(
            (*P(p.x, p.y), *P(p.x + p.w, p.y + p.h)),
            radius=10 * SCALE,
            fill=_mix(fill, CARD_FILL, alpha),
            outline=_mix(stroke, CARD_FILL, alpha),
            width=max(1, int(round(width * SCALE))),
        )
        for text, ty, is_label in p.text_rows():
            _text(
                d,
                (p.cx + ox, ty + oy),
                text,
                LABEL_SIZE if is_label else DETAIL_SIZE,
                _mix(TEXT_DARK if is_label else TEXT_MUTED, CARD_FILL, alpha),
                bold=is_label,
            )

    if spec.steps:
        step_obj = spec.steps[min(step, len(spec.steps) - 1)]
        prefix = f"{step + 1}/{len(spec.steps)}  "
        font = _font(int(14 * SCALE))
        caption = _drawable(step_obj.caption, int(14 * SCALE))
        full = prefix + caption
        total_w = d.textlength(full, font=font)
        x0 = (W * SCALE - total_w) / 2
        y = (H - 24) * SCALE
        d.text((x0, y), prefix, font=font, fill=_rgb(ACCENT), anchor="ls")
        d.text((x0 + d.textlength(prefix, font=font), y), caption,
               font=font, fill=_rgb(TEXT_DARK), anchor="ls")

    out_w = min(W, MAX_WIDTH)
    out_h = int(round(H * out_w / W))
    return img.resize((out_w, out_h), Image.Resampling.LANCZOS)


def _shared_palette(frames: list[Image.Image]) -> Image.Image:
    picks = frames[:: max(1, len(frames) // 6)] + [frames[-1]]
    w, h = picks[0].size
    montage = Image.new("RGB", (w, h * len(picks)))
    for i, frame in enumerate(picks):
        montage.paste(frame, (0, i * h))
    return montage.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def render_gif(layout: Layout) -> bytes:
    frames: list[Image.Image] = []
    durations: list[int] = []
    n = max(1, layout.n_steps)
    for k in range(n):
        for i in range(1, DRAW_FRAMES + 1):
            frames.append(draw_frame(layout, k, i / DRAW_FRAMES))
            durations.append(DRAW_FRAME_MS)
        durations[-1] = FINAL_HOLD_MS if k == n - 1 else HOLD_MS
    palette = _shared_palette(frames)
    quantised = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    quantised[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=quantised[1:],
        duration=durations,
        loop=0,
        disposal=1,
    )
    return buf.getvalue()


def flow_to_gif(raw_or_spec) -> Optional[bytes]:
    spec = raw_or_spec if isinstance(raw_or_spec, FlowSpec) else FlowSpec.from_raw(raw_or_spec)
    if spec is None:
        return None
    return render_gif(layout_flow(spec))

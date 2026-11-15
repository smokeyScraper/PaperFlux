"""Export a flow diagram as a looping GIF (shareable "explainer" animation).

Uses the same ``Layout`` as the inline animated SVG, drawn frame-by-frame with
Pillow (see ``raster.py``).
"""
from __future__ import annotations

from typing import Optional

from PIL import Image, ImageDraw

from paperflux.src.visuals.flow import (
    ACCENT,
    BACK_EDGE_COLOR,
    CARD_FILL,
    CARD_STROKE,
    CHIP_FILL,
    DETAIL_SIZE,
    EDGE_COLOR,
    EXAMPLE_SIZE,
    KIND_COLORS,
    LABEL_SIZE,
    NOVEL_TAG,
    PACKET_R,
    PAD,
    TEXT_DARK,
    TEXT_MUTED,
    EdgeGeom,
    FlowSpec,
    Layout,
    layout_flow,
)
from paperflux.src.visuals.raster import MAX_WIDTH, SCALE, encode_gif
from paperflux.src.visuals.raster import drawable as _drawable
from paperflux.src.visuals.raster import font as _font
from paperflux.src.visuals.raster import mix as _mix
from paperflux.src.visuals.raster import partial as _partial
from paperflux.src.visuals.raster import rgb as _rgb
from paperflux.src.visuals.raster import text as _text

DRAW_FRAMES = 8
DRAW_FRAME_MS = 75
HOLD_MS = 1900
FINAL_HOLD_MS = 2600
DIM = 0.22


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

    packets: list[tuple[float, float]] = []
    for g in layout.edges:
        frac = edge_fraction(g)
        if frac <= 0:
            continue
        color = _rgb(BACK_EDGE_COLOR if g.back else EDGE_COLOR)
        pts = [P(x, y) for x, y in _partial(g.polyline(14), frac)]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=2 * SCALE, joint="curve")
        if frac < 0.999:
            packets.append(pts[-1])
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

    r = PACKET_R * SCALE
    for px, py in packets:
        d.ellipse((px - r - 2 * SCALE, py - r - 2 * SCALE, px + r + 2 * SCALE, py + r + 2 * SCALE),
                  fill=(255, 255, 255))
        d.ellipse((px - r, py - r, px + r, py + r), fill=_rgb(ACCENT))

    for p in layout.placed.values():
        s = layout.node_step[p.node.id]
        alpha = 1.0 if s < step else (DIM + (1 - DIM) * progress if s == step else DIM)
        arrived = s < step or (s == step and progress >= 0.999)
        fill, stroke = KIND_COLORS[p.node.kind]
        if p.node.novel:
            d.rounded_rectangle(
                (*P(p.x - 4, p.y - 4), *P(p.x + p.w + 4, p.y + p.h + 4)),
                radius=13 * SCALE,
                outline=_mix(ACCENT, CARD_FILL, 0.28 * alpha),
                width=3 * SCALE,
            )
            stroke = ACCENT
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
        tag = p.novel_tag()
        if tag:
            tx, ty, tw, th = tag
            d.rounded_rectangle((*P(tx, ty), *P(tx + tw, ty + th)), radius=th / 2 * SCALE,
                                fill=_mix(ACCENT, CARD_FILL, alpha))
            _text(d, (tx + tw / 2 + ox, ty + th - 4 + oy), NOVEL_TAG[0], NOVEL_TAG[1],
                  _mix("#ffffff", ACCENT, alpha), bold=True)
        box = p.example_box()
        if box and arrived:
            bx, by, bw, bh = box
            d.rounded_rectangle((*P(bx, by), *P(bx + bw, by + bh)), radius=6 * SCALE,
                                fill=_rgb(CHIP_FILL), outline=_mix(stroke, CHIP_FILL, 0.45), width=SCALE)
            for text, ty in p.example_rows():
                _text(d, (p.cx + ox, ty + oy), text, EXAMPLE_SIZE, _rgb(TEXT_DARK))

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


def render_gif(layout: Layout) -> bytes:
    frames: list[Image.Image] = []
    durations: list[int] = []
    n = max(1, layout.n_steps)
    for k in range(n):
        for i in range(1, DRAW_FRAMES + 1):
            frames.append(draw_frame(layout, k, i / DRAW_FRAMES))
            durations.append(DRAW_FRAME_MS)
        durations[-1] = FINAL_HOLD_MS if k == n - 1 else HOLD_MS
    return encode_gif(frames, durations)


def flow_to_gif(raw_or_spec) -> Optional[bytes]:
    spec = raw_or_spec if isinstance(raw_or_spec, FlowSpec) else FlowSpec.from_raw(raw_or_spec)
    if spec is None:
        return None
    return render_gif(layout_flow(spec))

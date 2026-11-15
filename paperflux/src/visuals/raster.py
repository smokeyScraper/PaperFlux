"""Pillow drawing primitives shared by every GIF renderer (flows, token sequences, charts).

Frames are drawn supersampled at ``SCALE`` and downsized, with no browser or system fonts.
"""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

SCALE = 2
MAX_WIDTH = 900


def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(fg: str, bg: str, alpha: float) -> tuple[int, int, int]:
    a = max(0.0, min(1.0, alpha))
    f, b = rgb(fg), rgb(bg)
    return tuple(int(round(b[i] + (f[i] - b[i]) * a)) for i in range(3))  # type: ignore[return-value]


@lru_cache(maxsize=16)
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


_ASCII_FALLBACK = {
    "×": "x", "·": ".", "…": "...", "–": "-", "—": "-", "−": "-", "’": "'", "‘": "'",
    "“": '"', "”": '"', "≥": ">=", "≤": "<=", "→": "->", "←": "<-", "↔": "<->",
    "≈": "~", "≠": "!=", "±": "+/-", "∞": "inf", "°": "deg", "µ": "u", "μ": "u",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "θ": "theta", "λ": "lambda",
    "σ": "sigma", "π": "pi", "ε": "eps", "∑": "sum", "∏": "prod", "√": "sqrt",
    "\u00a0": " ", "\u2009": " ", "\u200b": "",
}


def _glyph_bytes(face: ImageFont.FreeTypeFont, ch: str, size: int) -> bytes:
    canvas = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(canvas).text((2, 2), ch, font=face, fill=255)
    return canvas.tobytes()


@lru_cache(maxsize=4096)
def _glyph_ok(size: int, ch: str) -> bool:
    if ch.isspace():
        return True
    face = font(size)
    # U+FFFE is a non-character, so it renders as the font's .notdef (tofu) glyph.
    return _glyph_bytes(face, ch, size) != _glyph_bytes(face, "\ufffe", size)


def drawable(text: str, size: int) -> str:
    """Map symbols the bundled font lacks to ASCII (avoids tofu boxes in the GIF)."""
    return "".join(ch if _glyph_ok(size, ch) else _ASCII_FALLBACK.get(ch, "") for ch in text)


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    value: str,
    size: float,
    fill: tuple[int, int, int],
    anchor: str = "ms",
    bold: bool = False,
) -> None:
    """Draw text at unscaled coordinates ``xy``."""
    px = max(8, int(round(size * SCALE)))
    face = font(px)
    value = drawable(value, px)
    x, y = xy[0] * SCALE, xy[1] * SCALE
    draw.text((x, y), value, font=face, fill=fill, anchor=anchor)
    if bold:  # the bundled font has a single weight: fake bold with a 1px smear
        draw.text((x + 1, y), value, font=face, fill=fill, anchor=anchor)


def text_width(value: str, size: float) -> float:
    """Rendered width of ``value`` in unscaled pixels."""
    px = max(8, int(round(size * SCALE)))
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    return probe.textlength(drawable(value, px), font=font(px)) / SCALE


def partial(points: list[tuple[float, float]], fraction: float) -> list[tuple[float, float]]:
    """The first ``fraction`` of a polyline, by length."""
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
            out.append((points[i][0] + (points[i + 1][0] - points[i][0]) * t,
                        points[i][1] + (points[i + 1][1] - points[i][1]) * t))
            return out
        run += seg
        out.append(points[i + 1])
    return out


def canvas(width: float, height: float, fill: str, stroke: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A supersampled card with rounded border."""
    w, h = int(width) * SCALE, int(height) * SCALE
    img = Image.new("RGB", (w, h), rgb(fill))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((1, 1, w - 2, h - 2), radius=14 * SCALE, outline=rgb(stroke), width=SCALE)
    return img, draw


def finish(img: Image.Image, width: float, height: float) -> Image.Image:
    out_w = min(int(width), MAX_WIDTH)
    out_h = int(round(height * out_w / width))
    return img.resize((out_w, out_h), Image.Resampling.LANCZOS)


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _shared_palette(frames: list[Image.Image]) -> Image.Image:
    picks = frames[:: max(1, len(frames) // 6)] + [frames[-1]]
    w, h = picks[0].size
    montage = Image.new("RGB", (w, h * len(picks)))
    for i, frame in enumerate(picks):
        montage.paste(frame, (0, i * h))
    return montage.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def encode_gif(frames: list[Image.Image], durations: list[int]) -> bytes:
    """Looping GIF with one shared palette (no colour flicker between frames)."""
    palette = _shared_palette(frames)
    quantised = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    quantised[0].save(buf, format="GIF", save_all=True, append_images=quantised[1:],
                      duration=durations, loop=0, disposal=1)
    return buf.getvalue()

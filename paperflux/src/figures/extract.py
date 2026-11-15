"""Lift the authors' own figures out of a PDF.

The model never sees this step. Captions ("Figure 3: ...") are found in the text,
and the drawing or image sitting next to each caption is rendered to a PNG. Vector
diagrams (the usual architecture figure) are included, not only embedded photos.
"""
import logging
import re
from dataclasses import dataclass

import pymupdf

logger = logging.getLogger("paperflux.figures")

_BAND = 380  # points to search above (or below) a caption
_START = re.compile(
    r"^(?:figure|fig\.?)\s*(\d+)\s*([a-z])?\s*[:.\-–—]?\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class PaperFigure:
    number: int
    label: str
    caption: str
    page: int
    png: bytes
    area: float = 0.0


def extract_figures(pdf_path: str) -> list[PaperFigure] | None:
    """Every captioned figure we could render, in figure-number order.

    ``None`` means the PDF could not be read (try again later). An empty list means
    the file opened and no usable figure was found. The same label on a later page
    (a repeated caption) keeps the larger rendering.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        logger.info("Could not open %s for figures: %s", pdf_path, exc)
        return None
    try:
        found: dict[str, PaperFigure] = {}
        for index, page in enumerate(doc):
            for figure in _page_figures(page, index + 1):
                current = found.get(figure.label)
                if current is None or figure.area > current.area:
                    found[figure.label] = figure
        return sorted(found.values(), key=lambda fig: (fig.number, fig.label, fig.page))
    except Exception:
        logger.warning("Figure extraction failed for %s", pdf_path, exc_info=True)
        return None
    finally:
        doc.close()


def _page_figures(page, page_number: int) -> list[PaperFigure]:
    artwork = _artwork(page)
    figures = []
    for rect, number, suffix, caption in _captions(page):
        clip = _clip_near(rect, artwork, "above") or _clip_near(rect, artwork, "below")
        if clip is None or clip.width < 70 or clip.height < 48:
            continue
        png = _render(page, clip)
        if not png:
            continue
        label = f"Figure {number}{suffix}"
        figures.append(PaperFigure(
            number=number,
            label=label,
            caption=caption[:500],
            page=page_number,
            png=png,
            area=clip.width * clip.height,
        ))
    return figures


def _captions(page):
    lines = []
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            text = "".join(span.get("text", "") for span in line.get("spans") or []).strip()
            if text:
                lines.append((pymupdf.Rect(line["bbox"]), text))
    lines.sort(key=lambda item: (item[0].y0, item[0].x0))

    captions = []
    index = 0
    while index < len(lines):
        match = _START.match(lines[index][1])
        if not match:
            index += 1
            continue
        rect, text = lines[index]
        parts = [text]
        nxt = index + 1
        while nxt < len(lines):
            nrect, ntext = lines[nxt]
            if _START.match(ntext) or nrect.y0 > rect.y1 + 16:
                break
            if _overlap_x(rect, nrect) < 8:
                break
            parts.append(ntext)
            rect |= nrect
            nxt += 1
        body = _START.match(parts[0])
        rest = " ".join(p.strip() for p in [body.group(3), *parts[1:]] if p.strip())
        captions.append((rect, int(match.group(1)), (match.group(2) or "").lower(), _squash(rest)))
        index = nxt
    return captions


def _artwork(page) -> list[pymupdf.Rect]:
    rects = []
    try:
        rects.extend(pymupdf.Rect(r) for r in page.cluster_drawings(x_tolerance=12, y_tolerance=12))
    except Exception:
        logger.debug("No vector drawings on page %s", page.number)
    for info in page.get_image_info() or []:
        rects.append(pymupdf.Rect(info["bbox"]))
    page_area = max(page.rect.width * page.rect.height, 1)
    kept = []
    for rect in rects:
        if rect.width < 8 or rect.height < 8:
            continue
        if rect.width * rect.height > 0.9 * page_area:
            continue
        kept.append(rect)
    return kept


def _clip_near(caption, artwork, side: str):
    if side == "above":
        window = pymupdf.Rect(caption.x0 - 40, caption.y0 - _BAND, caption.x1 + 40, caption.y0 - 1)

        def close(rect):
            return rect.y1 <= caption.y0 + 6 and rect.y0 >= window.y0 - 4
    else:
        window = pymupdf.Rect(caption.x0 - 40, caption.y1 + 1, caption.x1 + 40, caption.y1 + _BAND)

        def close(rect):
            return rect.y0 >= caption.y1 - 6 and rect.y1 <= window.y1 + 4
    nearby = [rect for rect in artwork if close(rect) and _overlap_x(rect, window) >= 12]
    if not nearby:
        return None
    seed = max(nearby, key=lambda rect: rect.y1) if side == "above" else min(nearby, key=lambda rect: rect.y0)
    chosen = [seed]
    grew = True
    while grew:
        grew = False
        for rect in nearby:
            if any(rect == have or _gap(rect, have) <= 20 for have in chosen):
                if rect not in chosen:
                    chosen.append(rect)
                    grew = True
    clip = pymupdf.Rect(chosen[0])
    for rect in chosen[1:]:
        clip |= rect
    return clip


def _render(page, clip) -> bytes | None:
    zoom = min(2.0, 1100 / max(clip.width, 1))
    zoom = max(1.1, zoom)
    for _ in range(2):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        if _mostly_blank(pix):
            return None
        png = pix.tobytes("png")
        if len(png) <= 900_000:
            return png
        zoom *= 0.65
    return png


def _mostly_blank(pix) -> bool:
    samples = pix.samples
    channels = pix.n
    if channels < 3 or not samples:
        return True
    step = max(channels, (len(samples) // channels // 4000) * channels)
    white = total = 0
    for offset in range(0, len(samples) - channels, step):
        total += 1
        if samples[offset] > 245 and samples[offset + 1] > 245 and samples[offset + 2] > 245:
            white += 1
    return total == 0 or white / total > 0.97


def _overlap_x(a, b) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))


def _gap(a, b) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return max(dx, dy)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

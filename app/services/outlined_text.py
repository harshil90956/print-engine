from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Tuple

from fontTools.pens.qu2cuPen import Qu2CuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.ttLib import TTFont


Op = Tuple[str, Tuple[float, ...]]


def _build_kern_pairs(font: TTFont) -> dict[tuple[str, str], float]:
    try:
        if "kern" not in font:
            return {}
        table = font["kern"]
        pairs: dict[tuple[str, str], float] = {}
        for st in getattr(table, "kernTables", []) or []:
            if getattr(st, "coverage", 0) & 1 == 0:
                # horizontal kerning only
                continue
            for (left, right), val in (getattr(st, "kernTable", None) or {}).items():
                if val:
                    pairs[(str(left), str(right))] = float(val)
        return pairs
    except Exception:
        return {}


def _default_font_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "fonts" / "LiberationSans-Regular.ttf"


@lru_cache(maxsize=8)
def _load_font(font_path: str) -> TTFont:
    return TTFont(font_path, recalcBBoxes=False, recalcTimestamp=False)


def glyph_names_for_text(*, text: str, font_path: str | None = None) -> List[str]:
    fp = str(font_path or _default_font_path())
    font = _load_font(fp)
    cmap = font.getBestCmap() or {}
    out: List[str] = []
    for ch in str(text or ""):
        code = ord(ch)
        out.append(str(cmap.get(code) or ".notdef"))
    return out


def outline_text_ops_pt(*, text: str, font_size_pt: float, x_pt: float, y_pt: float, font_path: str | None = None) -> List[Op]:
    ops, _bbox, _adv = outline_text_ops_pt_with_metrics(text=text, font_size_pt=font_size_pt, x_pt=x_pt, y_pt=y_pt, font_path=font_path)
    return ops


def outline_text_ops_pt_with_metrics(
    *,
    text: str,
    font_size_pt: float,
    x_pt: float,
    y_pt: float,
    font_path: str | None = None,
) -> tuple[List[Op], dict[str, float], List[float]]:
    fp = str(font_path or _default_font_path())
    font = _load_font(fp)

    units_per_em = float(font["head"].unitsPerEm)
    if units_per_em <= 0:
        raise ValueError("INVALID_FONT_UNITS_PER_EM")

    scale = float(font_size_pt) / units_per_em

    cmap = font.getBestCmap() or {}
    glyph_set = font.getGlyphSet()
    glyf_table = font["glyf"]
    hmtx = font["hmtx"].metrics
    kern_pairs = _build_kern_pairs(font)

    raw_ops: List[Op] = []
    advances_pt: List[float] = []
    cursor_x_units = 0.0
    prev_glyph_name: str | None = None

    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    def _acc_pt(x: float, y: float) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if x < min_x:
            min_x = x
        if y < min_y:
            min_y = y
        if x > max_x:
            max_x = x
        if y > max_y:
            max_y = y

    for ch in str(text or ""):
        code = ord(ch)
        glyph_name = str(cmap.get(code) or ".notdef")

        if prev_glyph_name is not None:
            cursor_x_units += float(kern_pairs.get((prev_glyph_name, glyph_name), 0.0))

        try:
            aw, _lsb = hmtx.get(glyph_name, (0, 0))
        except Exception:
            aw = 0

        rec = DecomposingRecordingPen(glyph_set)
        try:
            ttglyph = glyf_table[glyph_name]
        except Exception:
            ttglyph = glyf_table[".notdef"]
        ttglyph.draw(rec, glyf_table)

        cubic_rec = RecordingPen()
        qu2cu = Qu2CuPen(cubic_rec, max_err=0.25, reverse_direction=False)
        rec.replay(qu2cu)

        for op, pts in cubic_rec.value:
            if op == "moveTo":
                x, y = pts[0]
                px = (cursor_x_units + float(x)) * scale
                py = float(y) * scale
                raw_ops.append(("moveTo", (px, py)))
                _acc_pt(px, py)
            elif op == "lineTo":
                x, y = pts[0]
                px = (cursor_x_units + float(x)) * scale
                py = float(y) * scale
                raw_ops.append(("lineTo", (px, py)))
                _acc_pt(px, py)
            elif op == "curveTo":
                (x1, y1), (x2, y2), (x3, y3) = pts
                p = (
                    (cursor_x_units + float(x1)) * scale,
                    float(y1) * scale,
                    (cursor_x_units + float(x2)) * scale,
                    float(y2) * scale,
                    (cursor_x_units + float(x3)) * scale,
                    float(y3) * scale,
                )
                raw_ops.append(("curveTo", p))
                _acc_pt(p[0], p[1])
                _acc_pt(p[2], p[3])
                _acc_pt(p[4], p[5])
            elif op == "closePath":
                raw_ops.append(("close", ()))
            else:
                continue

        advances_pt.append(float(aw) * scale)
        cursor_x_units += float(aw)
        prev_glyph_name = glyph_name

    if not raw_ops or min_x == float("inf"):
        return [], {"min_x": 0.0, "min_y": 0.0, "max_x": 0.0, "max_y": 0.0, "w": 0.0, "h": 0.0}, advances_pt

    # Normalize to bbox (min_x=min_y=0) to prevent any clipping.
    norm_ops: List[Op] = []
    w = max_x - min_x
    h = max_y - min_y

    # Locked behavior: treat y_pt as the baseline origin. Do NOT attempt bbox-based top alignment.
    base_x = float(x_pt)
    base_y = float(y_pt)

    for op, pts in raw_ops:
        if op in {"moveTo", "lineTo"}:
            nx = base_x + (float(pts[0]) - min_x)
            ny = base_y + (float(pts[1]) - min_y)
            norm_ops.append((op, (nx, ny)))
        elif op == "curveTo":
            nx1 = base_x + (float(pts[0]) - min_x)
            ny1 = base_y + (float(pts[1]) - min_y)
            nx2 = base_x + (float(pts[2]) - min_x)
            ny2 = base_y + (float(pts[3]) - min_y)
            nx3 = base_x + (float(pts[4]) - min_x)
            ny3 = base_y + (float(pts[5]) - min_y)
            norm_ops.append((op, (nx1, ny1, nx2, ny2, nx3, ny3)))
        else:
            norm_ops.append((op, ()))

    bbox = {
        "min_x": float(min_x),
        "min_y": float(min_y),
        "max_x": float(max_x),
        "max_y": float(max_y),
        "w": float(w),
        "h": float(h),
    }

    return norm_ops, bbox, advances_pt

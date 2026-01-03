from __future__ import annotations

import base64
import io
import os
import re
import tempfile
from reportlab.lib import colors
from pathlib import Path
from typing import Any, Dict

import boto3
import logging
from fontTools.ttLib import TTFont as FTFont
from pdfrw import PdfReader
from pdfrw.buildxobj import pagexobj
from pdfrw.toreportlab import makerl
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

from app.config import Settings
from app.services.normalize import svg_to_pdf_cached_original_size
from app.services.template import Template
from app.services.font_registry import resolve_font_family
from app.utils.units import mm_to_pt

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0
OBJECTS_PER_PAGE = 4

DEBUG_DRAW_OBJECT_BOX = False

BASELINE_CORRECTION_MM = 0.0

logger = logging.getLogger(__name__)


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    s = str(data_url or "")
    if not s.startswith("data:"):
        raise ValueError("Invalid data_url")

    header, _, payload = s.partition(",")
    if not payload:
        raise ValueError("Invalid data_url")

    mime = header[5:].split(";")[0] if header.startswith("data:") else ""
    is_base64 = ";base64" in header
    if is_base64:
        return base64.b64decode(payload.encode("ascii")), mime
    return payload.encode("utf-8"), mime


def _register_custom_fonts(custom_fonts: list[dict[str, Any]]) -> None:
    registered = set([str(n) for n in pdfmetrics.getRegisteredFontNames()])
    for f in custom_fonts or []:
        family = str(f.get("family") or "").strip()
        data_url = str(f.get("data_url") or "").strip()
        if not family or not data_url:
            continue
        if family in registered:
            continue

        raw_bytes, _mime_from_url = _decode_data_url(data_url)
        hint_mime = str(f.get("mime") or "").strip().lower()
        ext = "ttf"
        if "opentype" in hint_mime or hint_mime.endswith("/otf"):
            ext = "otf"
        elif "woff2" in hint_mime:
            ext = "woff2"
        elif "woff" in hint_mime:
            ext = "woff"

        with tempfile.TemporaryDirectory(prefix="pe_fonts_") as td:
            src_path = Path(td) / f"src.{ext}"
            src_path.write_bytes(raw_bytes)

            # ReportLab embeds TrueType/OpenType via TTFont. For WOFF/WOFF2, try to convert via fontTools.
            font_path: Path
            if ext in {"ttf", "otf"}:
                font_path = src_path
            else:
                try:
                    ft = FTFont(str(src_path), recalcBBoxes=False, recalcTimestamp=False)
                    out_path = Path(td) / "converted.ttf"
                    ft.flavor = None
                    ft.save(str(out_path))
                    font_path = out_path
                except Exception as e:
                    raise ValueError(f"CUSTOM_FONT_UNSUPPORTED_FORMAT: {family}") from e

            try:
                pdfmetrics.registerFont(RLTTFont(family, str(font_path)))
                registered.add(family)
                logger.info("CUSTOM_FONT_REGISTERED", extra={"family": family})
            except Exception as e:
                raise ValueError(f"CUSTOM_FONT_REGISTER_FAILED: {family}") from e


def _draw_overlay(
    *,
    canvas: Canvas,
    settings: Settings,
    overlay: dict[str, Any],
    object_x_pt: float,
    object_y_pt: float,
    object_h_pt: float,
) -> None:
    data_url = str(overlay.get("data_url") or "").strip()
    overlay_type = str(overlay.get("type") or "").strip().lower()
    svg_s3_key = str(overlay.get("svg_s3_key") or "").strip()
    if overlay_type == "svg" and svg_s3_key:
        x_mm = float(overlay.get("x_mm"))
        y_mm = float(overlay.get("y_mm"))
        scale = float(overlay.get("scale"))
        rot = float(overlay.get("rotation_deg") or 0.0)
        if scale <= 0:
            return

        _hash, overlay_pdf_path = svg_to_pdf_cached_original_size(settings=settings, svg_s3_key=svg_s3_key)
        ov_w_pt, ov_h_pt = _pdf_page_size_pt(str(overlay_pdf_path))
        if ov_w_pt <= 0 or ov_h_pt <= 0:
            raise ValueError("INVALID_OVERLAY_SVG")

        # Anchor is the *untransformed* intrinsic box top-left in object_mm space (same as editor).
        # To draw the PDF form in ReportLab (bottom-left origin), we convert that to bottom-left.
        x_pt = float(object_x_pt) + mm_to_pt(float(x_mm))
        y_top_pt = float(object_y_pt) + (float(object_h_pt) - mm_to_pt(float(y_mm)))
        y_bottom_pt = float(y_top_pt) - float(ov_h_pt)

        cx = float(ov_w_pt) / 2.0
        cy = float(ov_h_pt) / 2.0

        ov_pdf = PdfReader(str(overlay_pdf_path))
        ov_xobj = pagexobj(ov_pdf.pages[0])

        canvas.saveState()
        # Match the frontend CSS transform model:
        # - element layout box is intrinsic size at (x_mm, y_mm)
        # - then rotate+scale about center
        canvas.translate(float(x_pt), float(y_bottom_pt))
        canvas.translate(float(cx), float(cy))
        if rot:
            canvas.rotate(rot)
        canvas.scale(float(scale), float(scale))
        canvas.translate(-float(cx), -float(cy))
        canvas.doForm(makerl(canvas, ov_xobj))
        canvas.restoreState()
        return

    if not data_url:
        return

    mime = str(overlay.get("mime") or "").strip().lower()
    x_mm = float(overlay.get("x_mm"))
    y_mm = float(overlay.get("y_mm"))
    w_mm = float(overlay.get("w_mm"))
    h_mm = float(overlay.get("h_mm"))
    rot = float(overlay.get("rotation_deg") or 0.0)

    # Overlays are ABSOLUTE in object_mm space (top-left origin), and must not inherit
    # any SVG/background scaling. We only offset by the object's page position.
    w_pt = mm_to_pt(w_mm)
    h_pt = mm_to_pt(h_mm)
    x_pt = float(object_x_pt) + mm_to_pt(x_mm)
    y_bottom_pt = float(object_y_pt) + (float(object_h_pt) - mm_to_pt(y_mm) - float(h_pt))

    raw_bytes, mime_from_url = _decode_data_url(data_url)
    effective_mime = mime or (mime_from_url or "")

    canvas.saveState()
    # Rotate around top-left to match preview transformOrigin: 'top left'
    canvas.translate(float(x_pt), float(y_bottom_pt) + float(h_pt))
    if rot:
        canvas.rotate(rot)
    canvas.translate(0.0, -float(h_pt))

    if "svg" in effective_mime:
        try:
            import cairosvg
        except Exception as e:
            raise ValueError("SVG_OVERLAY_REQUIRES_CAIROSVG") from e

        with tempfile.TemporaryDirectory(prefix="pe_overlay_") as td:
            svg_path = Path(td) / "overlay.svg"
            pdf_path = Path(td) / "overlay.pdf"
            svg_path.write_bytes(raw_bytes)
            cairosvg.svg2pdf(bytestring=raw_bytes, write_to=str(pdf_path))
            ov_w_pt, ov_h_pt = _pdf_page_size_pt(str(pdf_path))
            if ov_w_pt <= 0 or ov_h_pt <= 0:
                raise ValueError("INVALID_OVERLAY_SVG")
            scale_x = float(w_pt) / float(ov_w_pt)
            scale_y = float(h_pt) / float(ov_h_pt)
            ov_pdf = PdfReader(str(pdf_path))
            ov_xobj = pagexobj(ov_pdf.pages[0])
            canvas.scale(scale_x, scale_y)
            canvas.doForm(makerl(canvas, ov_xobj))
    else:
        img = ImageReader(io.BytesIO(raw_bytes))
        canvas.drawImage(img, 0.0, 0.0, width=float(w_pt), height=float(h_pt), mask='auto', preserveAspectRatio=True)

    canvas.restoreState()


def _ensure_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)


def _parse_series_start(start: str) -> tuple[str, int, int]:
    # NOTE: Spaces inside series prefix are valid and must be preserved
    series_start = str(start)
    match = re.search(r"(\d+)$", series_start)
    if not match:
        raise ValueError("Series must end with a numeric part")
    number_part = match.group(1)
    prefix_part = series_start[: match.start()]
    return prefix_part, int(number_part), len(number_part)


def _series_value(prefix: str, base: int, width: int, i: int) -> str:
    n = base + i
    return f"{prefix}{str(n).zfill(width)}"


def _page_size_pt() -> tuple[float, float]:
    return mm_to_pt(A4_WIDTH_MM), mm_to_pt(A4_HEIGHT_MM)


def _pdf_page_size_pt(pdf_path: str) -> tuple[float, float]:
    p = Path(pdf_path)
    if not p.exists() or not p.is_file():
        raise RuntimeError("INVALID_BACKGROUND_PDF: file not found")
    with open(p, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise RuntimeError("INVALID_BACKGROUND_PDF: expected %PDF- header")
    pdf = PdfReader(pdf_path)
    if not pdf.pages:
        raise ValueError("background PDF has no pages")
    mb = pdf.pages[0].MediaBox
    if not mb or len(mb) != 4:
        raise ValueError("PDF MediaBox missing")
    w = float(mb[2]) - float(mb[0])
    h = float(mb[3]) - float(mb[1])
    return w, h


def _object_size_pt(object_mm: Dict[str, Any], slot_w_pt: float, slot_h_pt: float) -> tuple[float, float]:
    # Slot is the primary layout unit. User input defines an internal object box inside the slot.
    # object_mm.w/h define the physical print size of the object.
    w_mm = object_mm.get("w")
    h_mm = object_mm.get("h")

    if w_mm is None or h_mm is None:
        raise ValueError("object_mm.w and object_mm.h are required")

    w_pt = mm_to_pt(float(w_mm))
    h_pt = mm_to_pt(float(h_mm))
    if w_pt <= 0 or h_pt <= 0:
        raise ValueError("object_mm.w and object_mm.h must be > 0")
    return w_pt, h_pt


def write_final_pdf(
    *,
    template: Template,
    settings: Settings,
    job_id: str,
    output_path: str,
) -> tuple[int, str, Dict[str, Any]]:
    mode = str(getattr(template, "render_mode", "") or "").strip() or "legacy"

    series_cfg = template.series_config
    count = int(series_cfg.get("count"))
    if count <= 0:
        raise ValueError("series.count must be > 0")

    # Register session-scoped custom fonts before resolving requested font_family.
    _register_custom_fonts(list(getattr(template, "custom_fonts", []) or []))

    requested_font_family = str(series_cfg.get("font_family") or "").strip()
    resolved_font_family, font_source, embedded = resolve_font_family(requested_font_family)
    if requested_font_family and requested_font_family != resolved_font_family:
        logger.warning(
            "FONT_FAMILY_FALLBACK",
            extra={
                "requested_font_family": requested_font_family,
                "resolved_font_family": resolved_font_family,
                "font_source": font_source,
                "embedded": bool(embedded),
            },
        )

    logger.info(
        "FONT_FAMILY_RENDER",
        extra={
            "requested_font_family": requested_font_family,
            "resolved_font_family": resolved_font_family,
            "font_source": font_source,
            "embedded": bool(embedded),
        },
    )

    font_size_mm = float(series_cfg.get("font_size_mm"))
    if font_size_mm <= 0:
        raise ValueError("series.font_size_mm must be > 0")

    per_letter_sizes_mm_raw = series_cfg.get("per_letter_font_size_mm")
    per_letter_sizes_mm: list[float] | None = None
    if per_letter_sizes_mm_raw is not None:
        if not isinstance(per_letter_sizes_mm_raw, list):
            raise ValueError("series.per_letter_font_size_mm must be a list of numbers")
        per_letter_sizes_mm = []
        for v in per_letter_sizes_mm_raw:
            try:
                n = float(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                per_letter_sizes_mm.append(n)

    prefix, base, width = _parse_series_start(series_cfg.get("start"))

    out_path = Path(output_path)
    _ensure_dir(out_path.parent)

    deterministic_4up = False

    # A4 is the absolute authority.
    page_w_pt, page_h_pt = _page_size_pt()

    background_pdf_path = template.background_pdf_path
    if str(background_pdf_path).lower().endswith(".svg"):
        _svg_hash, background_pdf_path = svg_to_pdf_cached_original_size(settings=settings, svg_s3_key=background_pdf_path)
    p = Path(background_pdf_path)
    if not p.exists() or not p.is_file():
        raise RuntimeError("INVALID_BACKGROUND_PDF: file not found")
    with open(p, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise RuntimeError("INVALID_BACKGROUND_PDF: expected %PDF- header")

    # Load normalized SVG-PDF once (vector). We use its MediaBox as source size.
    # IMPORTANT: MediaBox is used ONLY to compute a deterministic transform to reach the
    # user-specified physical size (object_mm -> pt). It must never override object_mm.
    svg_w_pt, svg_h_pt = _pdf_page_size_pt(background_pdf_path)
    if os.getenv("PRINT_ENGINE_DEBUG_SERIES") == "1":
        print(
            "PE_DEBUG svg_media_box_pt",
            {
                "background_pdf_path": str(background_pdf_path),
                "svg_w_pt": float(svg_w_pt),
                "svg_h_pt": float(svg_h_pt),
            },
        )
    svg_pdf = PdfReader(background_pdf_path)
    svg_xobj = pagexobj(svg_pdf.pages[0])

    canvas = Canvas(str(out_path), pagesize=(page_w_pt, page_h_pt))
    font_size_pt = mm_to_pt(font_size_mm)
    logger.info("FONT_RENDER", {"font_size_mm": float(font_size_mm), "font_size_pt": float(font_size_pt), "has_per_letter": bool(per_letter_sizes_mm)})

    slot_w_pt = page_w_pt

    mode = str(getattr(template, "render_mode", "") or "").strip() or "legacy"

    object_box_cfg = template.object_box_mm or {}
    cut_margin_mm_raw = object_box_cfg.get("cut_margin_mm")
    try:
        cut_margin_mm = float(cut_margin_mm_raw) if cut_margin_mm_raw is not None else 0.0
    except (TypeError, ValueError):
        cut_margin_mm = 0.0
    if cut_margin_mm < 0:
        cut_margin_mm = 0.0
    cut_margin_pt = mm_to_pt(cut_margin_mm)

    if mode == "exact_mm":
        slot_h_pt = (page_h_pt - ((OBJECTS_PER_PAGE - 1) * cut_margin_pt)) / OBJECTS_PER_PAGE
    else:
        slot_h_pt = page_h_pt / OBJECTS_PER_PAGE
    slot_h_mm = slot_h_pt / mm_to_pt(1.0)

    total_pages = (count + (OBJECTS_PER_PAGE - 1)) // OBJECTS_PER_PAGE
    serial_index = 0
    engine_metrics: Dict[str, Any] = {
        "svg_media_box_pt": {"w": float(svg_w_pt), "h": float(svg_h_pt)},
    }

    for _page in range(total_pages):
        for slot_index in range(OBJECTS_PER_PAGE):
            if mode == "exact_mm":
                # Slot origin in user mm (measured from page top-left)
                slot_x_mm = 0.0
                slot_y_mm = float(slot_index) * float(slot_h_mm + cut_margin_mm)

                # Slot origin in reportlab pt (bottom-left)
                slot_x_pt = mm_to_pt(slot_x_mm)
                slot_y_top_pt = mm_to_pt(slot_y_mm)
                slot_y_pt = page_h_pt - slot_y_top_pt - slot_h_pt
            else:
                # Legacy: slot origin in reportlab pt (bottom-left)
                slot_x_pt = 0.0
                slot_y_pt = page_h_pt - ((slot_index + 1) * slot_h_pt)
                slot_y_top_pt = page_h_pt - slot_y_pt - slot_h_pt

            # Leave remaining slots blank when count < 4 or not divisible by 4.
            if serial_index >= count:
                continue

            # Object physical size is defined ONLY by object_mm.w/h.
            object_w_pt, object_h_pt = _object_size_pt(template.object_box_mm, slot_w_pt=slot_w_pt, slot_h_pt=slot_h_pt)
            if os.getenv("PRINT_ENGINE_DEBUG_SERIES") == "1":
                obj_cfg = template.object_box_mm or {}
                print(
                    "PE_DEBUG object_size",
                    {
                        "job_id": job_id,
                        "slot_index": int(slot_index),
                        "object_w_mm": obj_cfg.get("w"),
                        "object_h_mm": obj_cfg.get("h"),
                        "object_w_pt": float(object_w_pt),
                        "object_h_pt": float(object_h_pt),
                    },
                )
            if mode != "exact_mm":
                if object_w_pt > slot_w_pt or object_h_pt > slot_h_pt:
                    raise ValueError("Object size exceeds slot size")

            if mode == "exact_mm":
                x_mm = object_box_cfg.get("x_mm")
                if x_mm is None:
                    x_mm = object_box_cfg.get("x")
                x_offset_pt = mm_to_pt(float(x_mm)) if x_mm is not None else 0.0

                # x_mm = 0 is absolute, alignment must not interfere
                # Keep non-zero behavior identical.
                if x_mm is not None and float(x_mm) == 0.0:
                    alignment = str((object_box_cfg.get("alignment") or "center")).strip().lower()
                    if alignment == "right":
                        page_left_pt = 0.0
                        object_x_pt = (page_left_pt + float(page_w_pt)) - float(object_w_pt)
                    else:
                        page_left_pt = 0.0
                        object_x_pt = page_left_pt
                else:
                    alignment = str((object_box_cfg.get("alignment") or "center")).strip().lower()
                    if alignment == "left":
                        base_x = slot_x_pt
                    elif alignment == "right":
                        base_x = slot_x_pt + slot_w_pt - object_w_pt
                    else:
                        base_x = slot_x_pt + (slot_w_pt - object_w_pt) / 2
                    object_x_pt = base_x + x_offset_pt

                y_mm = object_box_cfg.get("y_mm")
                if y_mm is None:
                    y_mm = object_box_cfg.get("y")
                y_offset_pt = mm_to_pt(float(y_mm)) if y_mm is not None else 0.0
                object_y_pt = (page_h_pt - slot_y_top_pt) - object_h_pt - y_offset_pt
            else:
                # Legacy: center object inside slot.
                object_x_pt = slot_x_pt + (slot_w_pt - object_w_pt) / 2
                object_y_pt = slot_y_pt + (slot_h_pt - object_h_pt) / 2

            # Place the SVG-derived PDF page as a form (vector placement).
            # We explicitly do NOT use any raster/image drawing APIs.
            form_name = f"bg_{template.template_id}"
            if not canvas.getAvailableFonts():
                # no-op; reportlab requires font machinery initialized
                pass

            if svg_w_pt <= 0 or svg_h_pt <= 0:
                raise ValueError("SVG-PDF MediaBox must be > 0")
            scale_x = object_w_pt / svg_w_pt
            scale_y = object_h_pt / svg_h_pt

            if os.getenv("PRINT_ENGINE_DEBUG_SERIES") == "1":
                print(
                    "PE_DEBUG scale",
                    {
                        "job_id": job_id,
                        "slot_index": int(slot_index),
                        "scale_x": float(scale_x),
                        "scale_y": float(scale_y),
                        "scale_equal": bool(scale_x == scale_y),
                    },
                )

            # Print engine rule:
            # User-provided mm dimensions are authoritative.
            # SVG content may stretch or distort to guarantee physical size accuracy.

            # Background is clipped to slot/object bounds.
            canvas.saveState()
            if mode == "exact_mm":
                p_slot = canvas.beginPath()
                p_slot.rect(slot_x_pt, slot_y_pt, slot_w_pt, slot_h_pt)
                canvas.clipPath(p_slot, stroke=0, fill=0)
            else:
                p = canvas.beginPath()
                p.rect(object_x_pt, object_y_pt, object_w_pt, object_h_pt)
                canvas.clipPath(p, stroke=0, fill=0)
            if DEBUG_DRAW_OBJECT_BOX:
                canvas.setLineWidth(0.5)
                canvas.rect(object_x_pt, object_y_pt, object_w_pt, object_h_pt, stroke=1, fill=0)

            if mode == "exact_mm":
                rotation_deg_raw = object_box_cfg.get("rotation_deg")
                try:
                    rotation_deg = float(rotation_deg_raw) if rotation_deg_raw is not None else 0.0
                except (TypeError, ValueError):
                    rotation_deg = 0.0
                canvas.translate(object_x_pt + (object_w_pt / 2.0), object_y_pt + (object_h_pt / 2.0))
                canvas.rotate(rotation_deg)
                canvas.scale(scale_x, scale_y)
                canvas.translate(-svg_w_pt / 2.0, -svg_h_pt / 2.0)
            else:
                canvas.translate(object_x_pt, object_y_pt)
                canvas.scale(scale_x, scale_y)

            canvas.doForm(makerl(canvas, svg_xobj))
            canvas.restoreState()

            # Draw overlays on top of the object (preview parity). These are independent of series.
            for ov in list(getattr(template, "overlays", []) or []):
                _draw_overlay(
                    canvas=canvas,
                    settings=settings,
                    overlay=ov,
                    object_x_pt=float(object_x_pt),
                    object_y_pt=float(object_y_pt),
                    object_h_pt=float(object_h_pt),
                )

            anchor_space = str(series_cfg.get("anchor_space") or "").strip().lower()
            x_mm_series = series_cfg.get("x_mm")
            y_mm_series = series_cfg.get("y_mm")
            if anchor_space != "object_mm" or x_mm_series is None or y_mm_series is None:
                raise ValueError("series placement invalid: requires anchor_space=object_mm and x_mm/y_mm")

            # MM-perfect placement in object coordinates (top-left origin from editor).
            # pdf_x_pt = object_x_pt + mm_to_pt(x_mm)
            # pdf_y_pt = object_y_pt + mm_to_pt(object_h_mm - y_mm)
            pdf_x_pt = float(object_x_pt) + mm_to_pt(float(x_mm_series))
            pdf_y_pt = float(object_y_pt) + (float(object_h_pt) - mm_to_pt(float(y_mm_series) + float(BASELINE_CORRECTION_MM)))

            series_rotation_deg = float(series_cfg.get("rotation_deg") or 0.0)
            letter_spacing_mm = float(series_cfg.get("letter_spacing_mm") or 0.0)
            series_color_raw = str(series_cfg.get("color") or "#000000").strip()
            try:
                fill_color = colors.HexColor(series_color_raw) if series_color_raw.startswith("#") else colors.toColor(series_color_raw)
            except Exception:
                fill_color = colors.black

            serial = _series_value(prefix, base, width, serial_index)
            serial_index += 1

            if os.getenv("PRINT_ENGINE_DEBUG_SERIES") == "1":
                print("SERIES_PREVIEW_MM", {"x_mm": float(x_mm_series), "y_mm": float(y_mm_series)})
                print("SERIES_OUTPUT_PT", {"x_pt": float(pdf_x_pt), "y_pt": float(pdf_y_pt)})
                print("SERIES_STRING", {"text": str(serial)})
                print("FONT_SIZE_MM", {"font_size_mm": float(font_size_mm)})

            # Draw series as a clean PDF overlay: no clip, no scale, baseline anchored.
            canvas.saveState()
            canvas.translate(float(pdf_x_pt), float(pdf_y_pt))
            if float(series_rotation_deg) != 0.0:
                canvas.rotate(float(series_rotation_deg))

            text_obj = canvas.beginText()
            text_obj.setTextOrigin(0.0, 0.0)
            text_obj.setFont(str(resolved_font_family), float(font_size_pt))
            text_obj.setFillColor(fill_color)

            advance_pt = mm_to_pt(float(letter_spacing_mm))
            for i, ch in enumerate(str(serial)):
                size_mm = float(per_letter_sizes_mm[i]) if per_letter_sizes_mm and i < len(per_letter_sizes_mm) else float(font_size_mm)
                size_pt = mm_to_pt(size_mm)
                text_obj.setFont(str(resolved_font_family), float(size_pt))
                text_obj.textOut(ch)
                if advance_pt:
                    text_obj.moveCursor(float(advance_pt), 0.0)

            canvas.drawText(text_obj)
            canvas.restoreState()

            if serial_index == 1:
                engine_metrics.update(
                    {
                        "object_mm": dict(template.object_box_mm or {}),
                        "object_pt": {"w": float(object_w_pt), "h": float(object_h_pt)},
                        "object_origin_pt": {"x": float(object_x_pt), "y": float(object_y_pt)},
                        "scale": {"x": float(scale_x), "y": float(scale_y)},
                        "series_anchor_space": (anchor_space or None),
                        "series_svg_pt": {"x": float(mm_to_pt(float(x_mm_series))), "y": float(svg_h_pt - mm_to_pt(float(y_mm_series)))},
                        "series_pdf_pt": {"x": float(pdf_x_pt), "y": float(pdf_y_pt)},
                    }
                )

        canvas.showPage()

    canvas.save()

    return total_pages, str(out_path), engine_metrics


def upload_pdf_to_s3(*, settings: Settings, local_path: str, s3_key: str) -> None:
    session = boto3.session.Session(
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION or None,
    )
    client = session.client("s3", endpoint_url=settings.S3_ENDPOINT or None)
    client.upload_file(local_path, settings.S3_BUCKET, s3_key, ExtraArgs={"ContentType": "application/pdf"})

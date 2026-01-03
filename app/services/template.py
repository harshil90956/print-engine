from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from app.utils.hash import sha256_hex


@dataclass(frozen=True)
class Template:
    template_id: str
    background_pdf_path: str
    object_box_mm: Dict[str, Any]
    series_config: Dict[str, Any]
    custom_fonts: list[Dict[str, Any]]
    overlays: list[Dict[str, Any]]
    render_mode: str


def _ensure_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)


def compute_template_id(
    *,
    svg_hash: str,
    object_mm: Dict[str, Any],
    series: Dict[str, Any],
    custom_fonts: list[Dict[str, Any]] | None,
    overlays: list[Dict[str, Any]] | None,
    render_mode: str,
) -> str:
    payload = {
        "svg_hash": svg_hash,
        "object_mm": object_mm,
        "series": series,
        "custom_fonts": custom_fonts or [],
        "overlays": overlays or [],
        "render_mode": render_mode,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_hex(raw)


def load_or_create_template(
    *,
    template_id: str,
    background_pdf_path: str,
    object_mm: Dict[str, Any],
    series: Dict[str, Any],
    custom_fonts: list[Dict[str, Any]] | None,
    overlays: list[Dict[str, Any]] | None,
    render_mode: str,
    cache_dir: str = "tmp/templates",
) -> Template:
    out_dir = Path(cache_dir)
    _ensure_dir(out_dir)

    meta_path = out_dir / f"{template_id}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return Template(
            template_id=meta["template_id"],
            background_pdf_path=meta["background_pdf_path"],
            object_box_mm=meta["object_box_mm"],
            series_config=meta["series_config"],
            custom_fonts=meta.get("custom_fonts") or [],
            overlays=meta.get("overlays") or [],
            render_mode=meta.get("render_mode") or "legacy",
        )

    meta = {
        "template_id": template_id,
        "background_pdf_path": background_pdf_path,
        "object_box_mm": object_mm,
        "series_config": series,
        "custom_fonts": custom_fonts or [],
        "overlays": overlays or [],
        "render_mode": render_mode,
    }
    meta_path.write_text(json.dumps(meta, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    return Template(
        template_id=template_id,
        background_pdf_path=background_pdf_path,
        object_box_mm=object_mm,
        series_config=series,
        custom_fonts=custom_fonts or [],
        overlays=overlays or [],
        render_mode=render_mode,
    )

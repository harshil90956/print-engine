from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fontTools.ttLib import TTFont as FTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont


_PDF_CORE_FONTS: list[str] = [
    "Courier",
    "Courier-Bold",
    "Courier-Oblique",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Bold",
    "Times-Italic",
    "Times-BoldItalic",
    "Symbol",
    "ZapfDingbats",
]


def _system_font_dirs() -> list[Path]:
    if os.name == "nt":
        return [Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts"]

    home = Path.home()
    if sys.platform == "darwin":
        return [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            home / "Library" / "Fonts",
        ]

    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home / ".fonts",
        home / ".local" / "share" / "fonts",
    ]


def _iter_font_files() -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for d in _system_font_dirs():
        try:
            if not d.exists() or not d.is_dir():
                continue
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                if ext not in {".ttf", ".otf"}:
                    continue
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
        except Exception:
            continue
    return out


def _font_family_from_file(path: Path) -> Optional[str]:
    try:
        font = FTFont(str(path), recalcBBoxes=False, recalcTimestamp=False)
    except Exception:
        return None

    try:
        name_table = font["name"]
    except Exception:
        return None

    best: Optional[str] = None
    for rec in getattr(name_table, "names", []) or []:
        if getattr(rec, "nameID", None) != 1:
            continue
        try:
            value = rec.toUnicode()
        except Exception:
            try:
                value = str(rec.string, errors="ignore")
            except Exception:
                continue
        value = str(value)
        if not value:
            continue
        best = value
        if getattr(rec, "platformID", None) == 3:
            break
    return best


def _font_embeddable(path: Path) -> bool:
    try:
        font = FTFont(str(path), recalcBBoxes=False, recalcTimestamp=False)
        os2 = font.get("OS/2")
        fs_type = int(getattr(os2, "fsType", 0) or 0) if os2 is not None else 0
        restricted = bool(fs_type & 0x0002)
        return not restricted
    except Exception:
        return True


@lru_cache(maxsize=1)
def get_font_registry() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for name in _PDF_CORE_FONTS:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"family": name, "source": "pdf-core", "path": None, "embeddable": False})

    for p in _iter_font_files():
        family = _font_family_from_file(p)
        if not family:
            continue
        key = family.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"family": family, "source": "system", "path": str(p), "embeddable": bool(_font_embeddable(p))})

    out.sort(key=lambda x: str(x.get("family") or "").lower())
    return out


def resolve_font_family(requested_family: str) -> tuple[str, str, bool]:
    requested = str(requested_family or "").strip()
    if not requested:
        return "Helvetica", "pdf-core", False

    registry = get_font_registry()

    registered = set([str(n) for n in pdfmetrics.getRegisteredFontNames()])
    if requested in registered or requested in _PDF_CORE_FONTS:
        return requested, ("pdf-core" if requested in _PDF_CORE_FONTS else "registered"), (requested not in _PDF_CORE_FONTS)

    hit: Optional[dict[str, Any]] = None
    for f in registry:
        if str(f.get("family") or "").lower() == requested.lower():
            hit = f
            break

    if not hit:
        return "Helvetica", "pdf-core", False

    path = str(hit.get("path") or "").strip()
    embeddable = bool(hit.get("embeddable"))
    if not path or not embeddable:
        return "Helvetica", str(hit.get("source") or "unknown"), False

    try:
        pdfmetrics.registerFont(RLTTFont(str(hit.get("family")), path))
        return str(hit.get("family")), str(hit.get("source")), True
    except Exception:
        return "Helvetica", str(hit.get("source") or "system"), False

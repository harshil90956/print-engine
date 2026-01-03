from __future__ import annotations

from pathlib import Path

import boto3
import cairosvg

from app.config import Settings
from app.utils.hash import sha256_hex

SVG_TO_PDF_VERSION = "orig_v1"


def _s3_client(settings: Settings):
    session = boto3.session.Session(
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION or None,
    )

    # For S3-compatible endpoints, boto3 expects endpoint_url.
    endpoint_url = settings.S3_ENDPOINT or None
    return session.client("s3", endpoint_url=endpoint_url)


def download_s3_object_bytes(settings: Settings, key: str) -> bytes:
    client = _s3_client(settings)
    obj = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
    body = obj["Body"]
    return body.read()


def _read_svg_bytes(settings: Settings, svg_s3_key: str) -> bytes:
    p = Path(svg_s3_key)
    if p.exists() and p.is_file():
        return p.read_bytes()
    return download_s3_object_bytes(settings, svg_s3_key)


def _ensure_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)


def normalize_svg_to_a4_pdf_cached(
    *,
    settings: Settings,
    svg_s3_key: str,
    cache_dir: str = "tmp/templates",
) -> tuple[str, str]:
    # Backward-compatible entrypoint.
    # NOTE: Despite the name, this function no longer normalizes to A4.
    return svg_to_pdf_cached_original_size(settings=settings, svg_s3_key=svg_s3_key, cache_dir=cache_dir)


def svg_to_pdf_cached_original_size(
    *,
    settings: Settings,
    svg_s3_key: str,
    cache_dir: str = "tmp/templates",
) -> tuple[str, str]:
    # Convert SVG -> PDF while preserving the SVG's own dimensions.
    # No resizing/normalization is applied before placement.
    # INVARIANT (LOCKED): Do not inject A4 width/height. Do not modify viewBox.
    # Physical sizing is enforced only at placement time (object_mm -> pt in pdf_writer.py).
    svg_bytes = _read_svg_bytes(settings, svg_s3_key)
    svg_hash = sha256_hex(svg_bytes)

    out_dir = Path(cache_dir)
    _ensure_dir(out_dir)

    cached_pdf_path = out_dir / f"{svg_hash}_{SVG_TO_PDF_VERSION}.pdf"
    if cached_pdf_path.exists():
        try:
            with open(cached_pdf_path, "rb") as f:
                head = f.read(5)
            if head == b"%PDF-":
                return svg_hash, str(cached_pdf_path)
        except OSError:
            pass
        try:
            cached_pdf_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Vector paths are preserved. Any embedded raster <image> stays as-is (no extraction).
    cairosvg.svg2pdf(bytestring=svg_bytes, write_to=str(cached_pdf_path))

    try:
        with open(cached_pdf_path, "rb") as f:
            head = f.read(5)
    except OSError:
        head = b""

    if head != b"%PDF-":
        try:
            cached_pdf_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("INVALID_SVG_TO_PDF_OUTPUT: expected PDF")

    return svg_hash, str(cached_pdf_path)

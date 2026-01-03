from pathlib import Path
from app.config import Settings
from app.services.normalize import svg_to_pdf_cached_original_size
from app.services.pdf_writer import upload_pdf_to_s3, write_final_pdf
from app.services.template import compute_template_id, load_or_create_template


def render_job(
    *,
    settings: Settings,
    job_id: str,
    svg_s3_key: str,
    object_mm: dict,
    series: dict,
    custom_fonts: list[dict] | None = None,
    overlays: list[dict] | None = None,
    render_mode: str | None = None,
) -> dict:
    object_mm = object_mm or {}
    raw_mode = str(render_mode or '').strip()
    if raw_mode in {"preview", "print_authoritative"}:
        mode = "exact_mm"
    elif raw_mode in {"deterministic_outlined", "deterministic_outlined_4up"}:
        mode = "exact_mm"
    else:
        mode = raw_mode or 'exact_mm'
    svg_hash, background_pdf_path = svg_to_pdf_cached_original_size(
        settings=settings,
        svg_s3_key=svg_s3_key,
    )

    template_id = compute_template_id(
        svg_hash=svg_hash,
        object_mm=object_mm,
        series=series,
        custom_fonts=custom_fonts,
        overlays=overlays,
        render_mode=mode,
    )
    template = load_or_create_template(
        template_id=template_id,
        background_pdf_path=background_pdf_path,
        object_mm=object_mm,
        series=series,
        custom_fonts=custom_fonts,
        overlays=overlays,
        render_mode=mode,
    )

    tmp_dir = Path("tmp")
    if not tmp_dir.exists():
        tmp_dir.mkdir(parents=True, exist_ok=True)

    final_local_path = str(tmp_dir / f"final_{job_id}.pdf")
    pages, _, engine_metrics = write_final_pdf(template=template, settings=settings, job_id=job_id, output_path=final_local_path)

    pdf_s3_key = f"documents/final/{job_id}.pdf"
    upload_pdf_to_s3(settings=settings, local_path=final_local_path, s3_key=pdf_s3_key)

    return {
        "status": "DONE",
        "pdf_s3_key": pdf_s3_key,
        "pages": pages,
        "template_id": template_id,
        "engine_metrics": engine_metrics,
    }

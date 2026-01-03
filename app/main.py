import logging
import os

from fastapi import FastAPI, Header, HTTPException
from dotenv import load_dotenv

from app.config import load_settings
from app.schemas import RenderRequest, RenderResponse
from app.services.font_registry import get_font_registry
from app.services.render import render_job

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
settings = load_settings()

app = FastAPI(title="print-engine")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("GIT_COMMIT_SHA")
        or os.getenv("RENDER_GIT_COMMIT")
        or "unknown",
    }


@app.get("/fonts")
def fonts_endpoint(x_internal_key: str = Header(default="", alias="x-internal-key")) -> list[dict]:
    if x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    fonts = get_font_registry()
    return [
        {
            "family": str(f.get("family") or ""),
            "source": str(f.get("source") or "unknown"),
        }
        for f in fonts
        if str(f.get("family") or "").strip()
    ]


@app.post("/render", response_model=RenderResponse)
def render_endpoint(payload: RenderRequest, x_internal_key: str = Header(default="", alias="x-internal-key")) -> RenderResponse:
    if x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if os.getenv("PRINT_ENGINE_DEBUG_SERIES") == "1":
        logger.info(
            "print_engine_payload",
            extra={
                "job_id": payload.job_id,
                "render_mode": payload.render_mode,
                "svg_s3_key": payload.svg_s3_key,
                "object_mm": payload.object_mm.model_dump() if payload.object_mm is not None else {},
                "series": payload.series.model_dump() if payload.series is not None else {},
            },
        )

    try:
        result = render_job(
            settings=settings,
            job_id=payload.job_id,
            svg_s3_key=payload.svg_s3_key,
            object_mm=payload.object_mm.model_dump() if payload.object_mm is not None else {},
            series=payload.series.model_dump(),
            custom_fonts=[f.model_dump() for f in (payload.custom_fonts or [])] if payload.custom_fonts is not None else None,
            overlays=[o.model_dump() for o in (payload.overlays or [])] if payload.overlays is not None else None,
            render_mode=payload.render_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("/render", extra={"job_id": payload.job_id, "pages": result.get("pages"), "template_id": result.get("template_id")})
    return RenderResponse(**result)


@app.post("/generate", response_model=RenderResponse)
def generate_endpoint(payload: RenderRequest, x_internal_key: str = Header(default="", alias="x-internal-key")) -> RenderResponse:
    return render_endpoint(payload=payload, x_internal_key=x_internal_key)

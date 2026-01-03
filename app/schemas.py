from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict

class ObjectBoxMm(BaseModel):
    x: float | None = None
    y: float | None = None
    x_mm: float | None = None
    y_mm: float | None = None
    w: float | None = None
    h: float | None = None
    alignment: str | None = None
    rotation_deg: float | None = None
    keep_proportions: bool | None = None
    cut_margin_mm: float | None = None


class SeriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    count: int
    anchor_space: str
    font_family: str = "Helvetica"
    font_size_mm: float
    per_letter_font_size_mm: list[float] | None = None
    x_mm: float
    y_mm: float
    letter_spacing_mm: float = 0.0
    rotation_deg: float = 0.0
    color: str = "#000000"


class CustomFont(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str
    data_url: str
    mime: str


class OverlayImageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_url: str
    mime: str
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    rotation_deg: float = 0.0


class OverlaySvgConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "svg"
    x_mm: float
    y_mm: float
    scale: float
    rotation_deg: float = 0.0
    svg_s3_key: str


OverlayConfig = OverlayImageConfig | OverlaySvgConfig


class RenderRequest(BaseModel):
    job_id: str
    svg_s3_key: str
    object_mm: ObjectBoxMm | None = None
    series: SeriesConfig
    custom_fonts: list[CustomFont] | None = None
    overlays: list[OverlayConfig] | None = None
    render_mode: str | None = None


class RenderResponse(BaseModel):
    status: str
    pdf_s3_key: str
    pages: int
    template_id: str
    engine_metrics: dict[str, Any] | None = None

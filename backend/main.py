from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List, Literal, Optional

from config import settings
from graph import run_compliance_graph
from logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class MaxRequestSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > settings.MAX_REQUEST_SIZE:
                raise HTTPException(status_code=413, detail="Payload too large (max 10 MB).")
        return await call_next(request)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(MaxRequestSizeMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AuditRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=50_000, description="Raw ledger or invoice text")
    selected_model: str = Field(default="qwen2.5:7b", description="Ollama model identifier")
    value_threshold: float = Field(default=15000.0, ge=0, le=1_000_000_000, description="Capital controls threshold (USD)")
    blacklist: List[str] = Field(default_factory=list, description="Restricted jurisdictions or entity names")
    review_action: Optional[Literal["approve", "reject", "escalate"]] = Field(
        default=None, description="Human reviewer override action"
    )
    review_notes: Optional[str] = Field(
        default=None, max_length=2000, description="Reviewer notes for override or escalation"
    )

    @field_validator("blacklist")
    @classmethod
    def limit_blacklist(cls, v: List[str]) -> List[str]:
        if len(v) > 500:
            raise ValueError("Blacklist cannot exceed 500 entries.")
        return v

    @field_validator("selected_model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("selected_model cannot be empty.")
        return v


class AuditResponse(BaseModel):
    extracted_vendor: str
    extracted_country: str
    extracted_amount: float
    violations: List[str]
    status: str
    justification: str
    next_steps: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["System"], summary="Service root")
def root() -> dict:
    return {"status": "running", "service": settings.API_TITLE, "version": settings.API_VERSION}


@app.get("/health", tags=["System"], summary="Health check")
def health_check() -> dict:
    return {"status": "healthy", "service": settings.API_TITLE}


@app.post(
    "/api/v1/audit",
    response_model=AuditResponse,
    tags=["Audit"],
    summary="Run the compliance audit pipeline",
    response_description="Compliance decision with extracted fields, violations, summary, and next steps",
)
def process_audit_request(payload: AuditRequest):
    logger.info(
        "Audit request received — model=%s threshold=%.2f blacklist_entries=%d",
        payload.selected_model,
        payload.value_threshold,
        len(payload.blacklist),
    )
    try:
        result = run_compliance_graph(payload.model_dump())
        logger.info(
            "Audit completed — status=%s vendor=%s country=%s",
            result.get("status"),
            result.get("extracted_vendor"),
            result.get("extracted_country"),
        )
        return result
    except Exception:
        logger.exception("Audit processing failed.")
        raise HTTPException(
            status_code=500,
            detail="Audit processing error. Check backend logs for details.",
        )

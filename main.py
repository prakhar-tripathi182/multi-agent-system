"""
FastAPI app exposing the pipeline as a Server-Sent Events (SSE) stream and PDF export.
Run locally with: uvicorn main:app --reload
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from pdf_generator import PDFGenerationError, build_report_pdf
from streaming_pipeline import stream_research_pipeline

load_dotenv()

app = FastAPI()

# CORS: allow the Vite dev server by default, or comma-separated origins in ALLOWED_ORIGINS
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets the browser JS read the suggested filename on the PDF response
    expose_headers=["Content-Disposition"],
)

# ---- Lightweight in-memory rate limiting ----
# Limitation: each worker process keeps its own counters in memory, so in a
# multi-worker (e.g. `uvicorn --workers 4`) or multi-instance deployment the
# effective per-client limit is multiplied by the number of workers/instances
# and resets on process restart.
_RATE_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_RESEARCH_LIMIT = int(os.getenv("RESEARCH_RATE_LIMIT", "5"))
_PDF_LIMIT = int(os.getenv("PDF_RATE_LIMIT", "15"))
_TRUST_PROXY = os.getenv("TRUST_PROXY", "").lower() in ("1", "true", "yes")


class _InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter keyed by client IP."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client_key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            bucket = self._hits[client_key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now) + 1)
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Please wait before sending another request.",
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

            # Periodically prune stale client keys so memory stays bounded
            if len(self._hits) > 1024:
                stale = [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]
                for k in stale:
                    del self._hits[k]


_research_limiter = _InMemoryRateLimiter(_RESEARCH_LIMIT, _RATE_WINDOW_SECONDS)
_pdf_limiter = _InMemoryRateLimiter(_PDF_LIMIT, _RATE_WINDOW_SECONDS)


def _client_ip(request: Request) -> str:
    """Return the client IP; trust X-Forwarded-For only when TRUST_PROXY is enabled."""
    if _TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def sse_event_stream(topic: str):
    """Format each pipeline event as an SSE message: "data: <json>\\n\\n"."""
    try:
        for event in stream_research_pipeline(topic):
            yield f"data: {json.dumps(event)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'step': 'pipeline', 'status': 'error', 'detail': str(e)})}\n\n"


@app.get("/research")
def research(topic: str, request: Request):
    _research_limiter.check(_client_ip(request))
    if not topic or not topic.strip():
        raise HTTPException(status_code=422, detail="Topic must not be empty.")
    return StreamingResponse(
        sse_event_stream(topic.strip()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class PdfRequest(BaseModel):
    """Data the frontend already has: nothing is re-researched."""
    topic: str = "Research Report"
    report: str
    critique: Optional[str] = None


@app.post("/download-pdf")
def download_pdf(payload: PdfRequest, request: Request):
    _pdf_limiter.check(_client_ip(request))
    try:
        pdf_bytes = build_report_pdf(payload.topic, payload.report, payload.critique)
    except PDFGenerationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    # Safe filename: keep letters, digits, space, - and _, cap the length
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in payload.topic)
    safe_name = safe_name.strip().replace(" ", "_")[:50] or "research"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_report.pdf"'},
    )

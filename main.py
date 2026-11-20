"""
FastAPI app exposing the pipeline as a Server-Sent Events (SSE) stream.
Run locally with: uvicorn main:app --reload
"""

import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from pdf_generator import PDFGenerationError, build_report_pdf
from streaming_pipeline import stream_research_pipeline

app = FastAPI()

# CORS: a future browser frontend on another origin (Vite dev server default shown)
# would be blocked unless we explicitly allow it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets the browser JS read the suggested filename on the PDF response
    expose_headers=["Content-Disposition"],
)


def sse_event_stream(topic: str):
    """Format each pipeline event as an SSE message: "data: <json>\\n\\n"."""
    for event in stream_research_pipeline(topic):
        yield f"data: {json.dumps(event)}\n\n"


@app.get("/research")
def research(topic: str):
    return StreamingResponse(
        sse_event_stream(topic),
        media_type="text/event-stream",
    )


class PdfRequest(BaseModel):
    """Data the frontend already has: nothing is re-researched."""
    topic: str = "Research Report"
    report: str
    critique: Optional[str] = None


@app.post("/download-pdf")
def download_pdf(payload: PdfRequest):
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

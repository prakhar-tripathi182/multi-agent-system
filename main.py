"""
FastAPI app exposing the pipeline as a Server-Sent Events (SSE) stream.
Run locally with: uvicorn main:app --reload
"""

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from streaming_pipeline import stream_research_pipeline

app = FastAPI()

# CORS: a future browser frontend on another origin (Vite dev server default shown)
# would be blocked unless we explicitly allow it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
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

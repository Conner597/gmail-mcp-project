"""
FastAPI backend
- GET  /auth/login      → redirects to Google OAuth consent
- GET  /auth/callback   → exchanges code, stores refresh token in SQLite
- GET  /auth/status     → returns whether a token exists
- POST /auth/logout     → deletes stored token
- POST /chat/stream     → streams agent responses (SSE)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

# Add parent dir to path so we can import auth + gmail_mcp
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth.token_store_web import get_credentials_web, store_token_from_code, delete_token, has_token
from backend.agent_stream import stream_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backend] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Gmail MCP Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login():
    from auth.token_store_web import get_auth_url
    url = get_auth_url()
    return RedirectResponse(url)


@app.get("/auth/callback")
async def auth_callback(code: str, state: str = None, error: str = None):
    if error:
        return RedirectResponse(f"http://localhost:3000?error={error}")
    try:
        store_token_from_code(code, state)
        return RedirectResponse("http://localhost:3000?authed=1")
    except Exception as e:
        logger.exception("OAuth callback error")
        return RedirectResponse(f"http://localhost:3000?error=callback_failed")


@app.get("/auth/status")
async def auth_status():
    return JSONResponse({"authenticated": has_token()})


@app.post("/auth/logout")
async def auth_logout():
    delete_token()
    return JSONResponse({"status": "logged_out"})


# ── Chat route ────────────────────────────────────────────────────────────────

@app.post("/chat/stream")
async def chat_stream(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    if not has_token():
        return JSONResponse({"error": "not_authenticated"}, status_code=401)

    async def event_generator():
        try:
            async for chunk in stream_agent(messages):
                data = json.dumps(chunk)
                yield f"data: {data}\n\n"
        except Exception as e:
            logger.exception("Streaming error")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
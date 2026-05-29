"""
Web OAuth token store — server-side Authorization Code flow.
Uses google_auth_oauthlib's Flow with a redirect URI pointing to /auth/callback.
Stores refresh token in SQLite, one row per install (single-user).
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

_DB_PATH = Path(os.getenv("TOKEN_DB_PATH", "tokens.db"))
_CLIENT_SECRET_PATH = Path(os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json"))
_TOKEN_KEY = "web_default"
_REDIRECT_URI = "http://localhost:8000/auth/callback"


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tokens "
        "(key TEXT PRIMARY KEY, token_json TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def _load_token() -> Credentials | None:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT token_json FROM tokens WHERE key = ?", (_TOKEN_KEY,)
        ).fetchone()
    if not row:
        return None
    return Credentials.from_authorized_user_info(json.loads(row[0]), SCOPES)


def _save_token(creds: Credentials) -> None:
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (key, token_json) VALUES (?, ?)",
            (_TOKEN_KEY, creds.to_json()),
        )
        conn.commit()


def delete_token() -> None:
    with _db_conn() as conn:
        conn.execute("DELETE FROM tokens WHERE key = ?", (_TOKEN_KEY,))
        conn.commit()


def has_token() -> bool:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tokens WHERE key = ?", (_TOKEN_KEY,)
        ).fetchone()
    return row is not None


# ── OAuth flow ─────────────────────────────────────────────────────────────────

def _make_flow() -> Flow:
    if not _CLIENT_SECRET_PATH.exists():
        raise FileNotFoundError(
            f"OAuth client secret not found at '{_CLIENT_SECRET_PATH}'. "
            "Set GOOGLE_CLIENT_SECRET in your .env."
        )
    flow = Flow.from_client_secrets_file(
        str(_CLIENT_SECRET_PATH),
        scopes=SCOPES,
        redirect_uri=_REDIRECT_URI,
    )
    return flow


# Store flow state between login and callback
_flow_cache: dict = {}

def get_auth_url() -> str:
    flow = _make_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # Cache the flow so we can use it in the callback
    _flow_cache[state] = flow
    return auth_url


def store_token_from_code(code: str, state: str) -> None:
    flow = _flow_cache.pop(state, None)
    if flow is None:
        # Fallback: create a fresh flow (works if PKCE not enforced)
        flow = _make_flow()
    flow.fetch_token(code=code)
    _save_token(flow.credentials)
    logger.info("Web OAuth token stored in %s", _DB_PATH)


def get_credentials_web() -> Credentials:
    creds = _load_token()
    if not creds:
        raise RuntimeError("No token stored. User must authenticate first.")

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except RefreshError as exc:
            if "invalid_grant" in str(exc).lower():
                logger.warning("Refresh token expired (invalid_grant). User must re-authenticate.")
                delete_token()
                raise RuntimeError("Token expired. Please reconnect Gmail.") from exc
            raise

    raise RuntimeError("No valid credentials. Please reconnect Gmail.")
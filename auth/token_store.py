"""
OAuth token store backed by SQLite.
Handles first-run browser flow, persistence, and automatic refresh.
On invalid_grant (expired refresh token after 7-day Testing window),
detects the error and re-runs the consent flow cleanly.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

_DB_PATH = Path(os.getenv("TOKEN_DB_PATH", "tokens.db"))
_CLIENT_SECRET_PATH = Path(os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json"))
_TOKEN_KEY = "default"


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
    data = json.loads(row[0])
    return Credentials.from_authorized_user_info(data, SCOPES)


def _save_token(creds: Credentials) -> None:
    token_json = creds.to_json()
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (key, token_json) VALUES (?, ?)",
            (_TOKEN_KEY, token_json),
        )
        conn.commit()
    logger.info("Token saved to %s", _DB_PATH)


def _delete_token() -> None:
    with _db_conn() as conn:
        conn.execute("DELETE FROM tokens WHERE key = ?", (_TOKEN_KEY,))
        conn.commit()
    logger.info("Stale token deleted from %s", _DB_PATH)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_credentials() -> Credentials:
    """
    Return valid Gmail credentials, running the OAuth flow if needed.
    Automatically refreshes access tokens and handles invalid_grant by
    deleting the stale token and re-prompting for consent.
    """
    if not _CLIENT_SECRET_PATH.exists():
        raise FileNotFoundError(
            f"OAuth client secret not found at '{_CLIENT_SECRET_PATH}'. "
            "Download it from GCP Console → APIs & Services → Credentials "
            "and set GOOGLE_CLIENT_SECRET in your .env."
        )

    creds = _load_token()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            logger.info("Refreshing access token…")
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except RefreshError as exc:
            # invalid_grant — refresh token expired (7-day Testing window) or revoked
            error_msg = str(exc).lower()
            if "invalid_grant" in error_msg or "token has been expired" in error_msg:
                logger.warning(
                    "Refresh token is invalid or expired (invalid_grant). "
                    "This is expected if the OAuth consent screen is in Testing mode "
                    "and 7 days have passed. Re-running consent flow…"
                )
                print(
                    "\n⚠️  Your Gmail refresh token has expired (Google Testing mode "
                    "tokens expire after 7 days).\n"
                    "A browser window will open to re-authorize access.\n"
                )
                _delete_token()
                creds = None
            else:
                raise

    # No valid token — run the installed-app flow
    logger.info("Running OAuth installed-app flow…")
    flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_SECRET_PATH), SCOPES)
    # run_local_server opens a browser and listens on loopback
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds)
    logger.info("OAuth flow complete. Token stored in %s", _DB_PATH)
    return creds

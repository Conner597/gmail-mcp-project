"""
Thin wrapper around the Gmail REST API.
All methods return plain Python dicts / lists — no Google API objects leak out.
"""

import base64
import email as email_lib
import email.mime.text
import email.mime.multipart
import logging
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GmailClient:
    def __init__(self, creds: Credentials):
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._user = "me"

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _header(msg: dict, name: str) -> str:
        for h in msg.get("payload", {}).get("headers", []):
            if h["name"].lower() == name.lower():
                return h["value"]
        return ""

    @staticmethod
    def _decode_body(payload: dict) -> str:
        """Recursively extract plain-text body from a message payload."""
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        if mime_type.startswith("multipart/"):
            for part in payload.get("parts", []):
                result = GmailClient._decode_body(part)
                if result:
                    return result
        return ""

    @staticmethod
    def _summarise(msg: dict) -> dict:
        return {
            "id": msg.get("id"),
            "threadId": msg.get("threadId"),
            "snippet": msg.get("snippet", ""),
            "from": GmailClient._header(msg, "From"),
            "to": GmailClient._header(msg, "To"),
            "subject": GmailClient._header(msg, "Subject"),
            "date": GmailClient._header(msg, "Date"),
            "labelIds": msg.get("labelIds", []),
        }

    def _fetch_message(self, msg_id: str, fmt: str = "full") -> dict:
        return (
            self._service.users()
            .messages()
            .get(userId=self._user, id=msg_id, format=fmt)
            .execute()
        )

    # ── public API ────────────────────────────────────────────────────────────

    def search_messages(self, query: str, max_results: int = 10) -> dict:
        try:
            resp = (
                self._service.users()
                .messages()
                .list(userId=self._user, q=query, maxResults=max_results)
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Gmail API error: {e.status_code} {e.reason}") from e

        messages = resp.get("messages", [])
        if not messages:
            return {"messages": [], "total": 0}

        summaries = []
        for m in messages:
            full = self._fetch_message(m["id"])
            summaries.append(self._summarise(full))

        return {"messages": summaries, "total": len(summaries)}

    def list_messages(self, label_ids: list[str] = None, max_results: int = 10) -> dict:
        if label_ids is None:
            label_ids = ["INBOX"]
        try:
            resp = (
                self._service.users()
                .messages()
                .list(userId=self._user, labelIds=label_ids, maxResults=max_results)
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Gmail API error: {e.status_code} {e.reason}") from e

        messages = resp.get("messages", [])
        if not messages:
            return {"messages": [], "total": 0}

        summaries = []
        for m in messages:
            full = self._fetch_message(m["id"])
            summaries.append(self._summarise(full))

        return {"messages": summaries, "total": len(summaries)}

    def get_message(self, message_id: str) -> dict:
        try:
            msg = self._fetch_message(message_id)
        except HttpError as e:
            if e.status_code == 404:
                raise RuntimeError(f"Message not found: {message_id}") from e
            raise RuntimeError(f"Gmail API error: {e.status_code} {e.reason}") from e

        summary = self._summarise(msg)
        summary["body"] = self._decode_body(msg.get("payload", {}))
        return summary

    def get_thread(self, thread_id: str) -> dict:
        try:
            resp = (
                self._service.users()
                .threads()
                .get(userId=self._user, id=thread_id, format="full")
                .execute()
            )
        except HttpError as e:
            if e.status_code == 404:
                raise RuntimeError(f"Thread not found: {thread_id}") from e
            raise RuntimeError(f"Gmail API error: {e.status_code} {e.reason}") from e

        messages = resp.get("messages", [])
        result = []
        for msg in messages:
            s = self._summarise(msg)
            s["body"] = self._decode_body(msg.get("payload", {}))
            result.append(s)

        return {"thread_id": thread_id, "message_count": len(result), "messages": result}

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> dict:
        mime_msg = email.mime.text.MIMEText(body, "plain")
        mime_msg["To"] = to
        mime_msg["Subject"] = subject

        if reply_to_message_id:
            mime_msg["In-Reply-To"] = reply_to_message_id
            mime_msg["References"] = reply_to_message_id

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        draft_body: dict = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        try:
            draft = (
                self._service.users()
                .drafts()
                .create(userId=self._user, body=draft_body)
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Failed to create draft: {e.status_code} {e.reason}") from e

        return {
            "draft_id": draft.get("id"),
            "message_id": draft.get("message", {}).get("id"),
            "thread_id": draft.get("message", {}).get("threadId"),
            "status": "draft_created",
        }

    def list_labels(self) -> dict:
        try:
            resp = self._service.users().labels().list(userId=self._user).execute()
        except HttpError as e:
            raise RuntimeError(f"Gmail API error: {e.status_code} {e.reason}") from e

        labels = resp.get("labels", [])
        return {
            "labels": [
                {"id": lbl["id"], "name": lbl["name"], "type": lbl.get("type", "")}
                for lbl in labels
            ]
        }

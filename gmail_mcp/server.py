"""
Gmail MCP Server (stdio transport)
Exposes Gmail read + draft tools via the MCP protocol.
"""

import json
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
)

from auth.token_store import get_credentials
from gmail_mcp.gmail_client import GmailClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gmail-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

app = Server("gmail-mcp")


def get_client() -> GmailClient:
    creds = get_credentials()
    return GmailClient(creds)


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_messages",
            description=(
                "Search Gmail messages using a Gmail query string "
                "(e.g. 'is:unread after:2024/01/01', 'from:boss@example.com subject:report'). "
                "Returns a list of message summaries (id, threadId, snippet, from, subject, date)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query. Supports all Gmail operators.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of messages to return (default 10, max 50).",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_messages",
            description=(
                "List recent Gmail messages, optionally filtered by label. "
                "Returns summaries (id, threadId, snippet, from, subject, date)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "label_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by label IDs (e.g. ['INBOX', 'UNREAD']). Defaults to INBOX.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of messages to return (default 10, max 50).",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="get_message",
            description=(
                "Fetch the full content of a single Gmail message by its ID, "
                "including headers (from, to, subject, date) and decoded body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The Gmail message ID.",
                    },
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="get_thread",
            description=(
                "Fetch all messages in a Gmail thread by thread ID. "
                "Returns each message with headers and body, ordered oldest-first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "thread_id": {
                        "type": "string",
                        "description": "The Gmail thread ID.",
                    },
                },
                "required": ["thread_id"],
            },
        ),
        Tool(
            name="create_draft",
            description=(
                "Create a Gmail draft (does NOT send). "
                "Optionally reply in-thread by supplying thread_id and reply_to_message_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Plain-text email body.",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Thread ID to reply into (optional).",
                    },
                    "reply_to_message_id": {
                        "type": "string",
                        "description": "Message ID being replied to — used to set In-Reply-To header (optional).",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="list_labels",
            description="List all Gmail labels available in the account (system + user-created).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _ok(data: Any) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(data, indent=2))])


def _err(msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": msg}))],
        isError=True,
    )


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    logger.info("tool_call name=%s arguments=%s", name, arguments)
    try:
        client = get_client()

        if name == "search_messages":
            query = arguments.get("query", "")
            if not query:
                return _err("'query' is required")
            max_results = min(int(arguments.get("max_results", 10)), 50)
            result = client.search_messages(query, max_results)
            return _ok(result)

        elif name == "list_messages":
            label_ids = arguments.get("label_ids", ["INBOX"])
            max_results = min(int(arguments.get("max_results", 10)), 50)
            result = client.list_messages(label_ids, max_results)
            return _ok(result)

        elif name == "get_message":
            message_id = arguments.get("message_id", "").strip()
            if not message_id:
                return _err("'message_id' is required")
            result = client.get_message(message_id)
            return _ok(result)

        elif name == "get_thread":
            thread_id = arguments.get("thread_id", "").strip()
            if not thread_id:
                return _err("'thread_id' is required")
            result = client.get_thread(thread_id)
            return _ok(result)

        elif name == "create_draft":
            to = arguments.get("to", "").strip()
            subject = arguments.get("subject", "").strip()
            body = arguments.get("body", "").strip()
            if not to or not subject or not body:
                return _err("'to', 'subject', and 'body' are all required")
            result = client.create_draft(
                to=to,
                subject=subject,
                body=body,
                thread_id=arguments.get("thread_id"),
                reply_to_message_id=arguments.get("reply_to_message_id"),
            )
            return _ok(result)

        elif name == "list_labels":
            result = client.list_labels()
            return _ok(result)

        else:
            return _err(f"Unknown tool: {name}")

    except Exception as exc:
        logger.exception("tool error name=%s", name)
        return _err(str(exc))


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

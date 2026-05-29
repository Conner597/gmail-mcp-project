"""
Streaming agent for the web backend.
Yields dicts that get serialized to SSE by main.py.
Chunk types: text_delta, tool_use, tool_result, done, error
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import AsyncGenerator

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth.token_store_web import get_credentials_web

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful Gmail assistant. You have tools to search and read emails,
view threads, and create drafts. You never send emails — only drafts.

When the user asks about their email, use the tools to fetch real data before answering.
Be concise but complete. When creating drafts, confirm what you've drafted."""

MODEL = "claude-sonnet-4-6"


def _mcp_tool_to_anthropic(tool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


async def stream_agent(messages: list[dict]) -> AsyncGenerator[dict, None]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        yield {"type": "error", "content": "ANTHROPIC_API_KEY not set"}
        return

    # Validate credentials before starting MCP server
    try:
        get_credentials_web()
    except RuntimeError as e:
        yield {"type": "error", "content": str(e)}
        return

    client = anthropic.Anthropic(api_key=api_key)
    server_script = Path(__file__).parent.parent / "gmail_mcp" / "server.py"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_script)],
        env={
            **os.environ,
            # Point server to web token store
            "TOKEN_DB_PATH": os.getenv("TOKEN_DB_PATH", "tokens.db"),
            "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json"),
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            anthropic_tools = [_mcp_tool_to_anthropic(t) for t in tools_response.tools]

            conversation = list(messages)

            # Agentic loop
            while True:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=anthropic_tools,
                    messages=conversation,
                )

                assistant_content = []

                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                        # Stream text word by word for a nicer UX
                        for word in block.text.split(" "):
                            yield {"type": "text_delta", "content": word + " "}

                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                        yield {
                            "type": "tool_use",
                            "name": block.name,
                            "input": block.input,
                        }

                conversation.append({"role": "assistant", "content": assistant_content})

                if response.stop_reason != "tool_use":
                    break

                # Execute tool calls
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    try:
                        mcp_result = await session.call_tool(block.name, block.input)
                        result_text = "\n".join(
                            c.text for c in mcp_result.content if hasattr(c, "text")
                        )
                        is_error = mcp_result.isError or False
                    except Exception as exc:
                        result_text = json.dumps({"error": str(exc)})
                        is_error = True

                    yield {
                        "type": "tool_result",
                        "name": block.name,
                        "is_error": is_error,
                    }

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                        "is_error": is_error,
                    })

                conversation.append({"role": "user", "content": tool_results})

            yield {"type": "done"}
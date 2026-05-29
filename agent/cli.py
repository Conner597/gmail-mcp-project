"""
CLI Chat Agent
--------------
Spawns the Gmail MCP server over stdio, bridges its tool schemas into the
Anthropic Messages API, and runs a multi-turn chat loop with full logging.

Usage:
    python agent/cli.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_FILE = Path(os.getenv("AGENT_LOG_FILE", "agent_log.jsonl"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def _log_event(event_type: str, data: dict) -> None:
    """Append a structured log entry to the JSONL log file and print to stdout."""
    entry = {"ts": datetime.utcnow().isoformat(), "type": event_type, **data}
    line = json.dumps(entry)

    # Stream to stdout so you can tail -f agent_log.jsonl
    print(line, flush=True)

    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── MCP → Anthropic schema bridge ────────────────────────────────────────────

def _mcp_tool_to_anthropic(tool) -> dict:
    """Convert an MCP Tool object to the Anthropic API tool format."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


# ── Agent loop ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a helpful Gmail assistant. You have tools to search and read emails,
view threads, and create drafts. You never send emails — only drafts.

When the user asks about their email, use the tools to fetch real data before answering.
Be concise but complete. When creating drafts, confirm what you've drafted.
If a tool returns an error, explain it clearly and suggest what the user can do."""

MODEL = "claude-sonnet-4-6"


async def run_agent():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌  ANTHROPIC_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Path to the MCP server entry point
    server_script = Path(__file__).parent.parent / "gmail_mcp" / "server.py"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_script)],
        env={**os.environ},  # pass full env so token_store can find client_secret.json
    )

    print("🔌  Starting Gmail MCP server…", flush=True)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            logger.info("MCP session initialized")

            # Fetch tool schemas once at startup
            tools_response = await session.list_tools()
            anthropic_tools = [_mcp_tool_to_anthropic(t) for t in tools_response.tools]
            logger.info("Loaded %d tools: %s", len(anthropic_tools), [t["name"] for t in anthropic_tools])

            print(f"✅  Connected. {len(anthropic_tools)} tools available.")
            print("💬  Gmail Assistant ready. Type your message (Ctrl+C to quit).\n")

            conversation: list[dict] = []

            while True:
                # ── User input ────────────────────────────────────────────────
                try:
                    user_input = input("You: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nGoodbye!")
                    break

                if not user_input:
                    continue

                conversation.append({"role": "user", "content": user_input})
                _log_event("user_turn", {"content": user_input})

                # ── Agentic loop (handles multi-step tool use) ─────────────────
                while True:
                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=4096,
                        system=SYSTEM_PROMPT,
                        tools=anthropic_tools,
                        messages=conversation,
                    )

                    logger.info("stop_reason=%s", response.stop_reason)

                    # Collect assistant message (may contain text + tool_use blocks)
                    assistant_content = []

                    for block in response.content:
                        if block.type == "text":
                            assistant_content.append({"type": "text", "text": block.text})
                            _log_event("assistant_text", {"text": block.text})
                            print(f"\nAssistant: {block.text}\n")

                        elif block.type == "tool_use":
                            assistant_content.append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            })
                            _log_event("tool_use", {
                                "tool_use_id": block.id,
                                "name": block.name,
                                "input": block.input,
                            })
                            print(f"  🔧  [{block.name}] {json.dumps(block.input)}", flush=True)

                    conversation.append({"role": "assistant", "content": assistant_content})

                    # If no tool calls, we're done with this turn
                    if response.stop_reason != "tool_use":
                        break

                    # ── Execute tool calls ─────────────────────────────────────
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue

                        try:
                            mcp_result = await session.call_tool(block.name, block.input)
                            # Flatten content blocks to a single string
                            result_text = "\n".join(
                                c.text for c in mcp_result.content if hasattr(c, "text")
                            )
                            is_error = mcp_result.isError or False
                        except Exception as exc:
                            result_text = json.dumps({"error": str(exc)})
                            is_error = True
                            logger.exception("MCP tool call failed: %s", block.name)

                        _log_event("tool_result", {
                            "tool_use_id": block.id,
                            "name": block.name,
                            "is_error": is_error,
                            "content_preview": result_text[:300],
                        })

                        if is_error:
                            print(f"  ❌  [{block.name}] error: {result_text[:200]}", flush=True)
                        else:
                            print(f"  ✅  [{block.name}] returned {len(result_text)} chars", flush=True)

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                            "is_error": is_error,
                        })

                    conversation.append({"role": "user", "content": tool_results})


def main():
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()

# Gmail MCP Agent

A Gmail MCP server + CLI chat agent (with optional web UI) built with the official MCP and Anthropic SDKs.

- **Gmail MCP server** (stdio) exposing 6 tools: `search_messages`, `list_messages`, `get_message`, `get_thread`, `create_draft`, `list_labels`
- **CLI agent** powered by `claude-sonnet-4-6` with full tool-call logging
- **OAuth 2.0** installed-app flow with SQLite token persistence and automatic refresh
- **Stretch: FastAPI + Next.js web UI** with streaming chat and server-side OAuth flow

> **No emails are ever sent.** The agent creates drafts only.

---

## Prerequisites

- Python 3.11 or 3.12 (Python 3.13+ is not yet fully supported by all dependencies)
- A Google account
- Node.js 18+ (required for the web UI; optional for MCP Inspector only if using CLI)

---

## 1. GCP Setup (one-time, ~5 minutes)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a new project (or use an existing one).

2. **Enable the Gmail API:**
   - Navigate to **APIs & Services → Library**
   - Search for "Gmail API" and click **Enable**

3. **Configure the OAuth consent screen:**

   > **Note:** Google recently updated the GCP console UI. If you see "Google Auth Platform not configured yet", click **Get started** and follow the short wizard — choose **External** for audience, fill in app name and contact email, and add your Gmail address as a test user when prompted. Once the wizard completes, create your Desktop OAuth client under **Clients** in the left sidebar (skip to step 4). If you see the classic UI, follow the steps below.

   - Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, click **Create**
   - Fill in App name (anything), User support email, Developer contact email
   - Click **Save and Continue** through Scopes (you can skip adding scopes here)
   - On the **Test users** step, click **+ Add users** and add your Gmail address
   - Click **Save and Continue**, then **Back to Dashboard**
   - Leave the app in **Testing** status (publishing is not needed)

4. **Create a Desktop OAuth client:**
   - Go to **APIs & Services → Credentials** (or **Clients** in the new UI)
   - Click **+ Create Credentials → OAuth client ID** (or **+ Create Client**)
   - Application type: **Desktop app**
   - Name it anything (e.g. "gmail-mcp-agent")
   - Click **Create**
   - Click **Download JSON** and save the file as `client_secret.json` in the project root
   - If using the web UI: under **Authorized redirect URIs**, click **+ Add URI** and add `http://localhost:8000/auth/callback`, then click **Save**
   - Make sure the filename is exactly `client_secret.json` with no double extension

> ⚠️ **Testing mode note:** Google expires refresh tokens after 7 days when the consent screen is in Testing status. This is expected behaviour. The agent detects `invalid_grant` errors, cleans up the stale token, and re-prompts for consent automatically. See [Expired token handling](#expired-token-handling) below.

---

## 2. Project Setup

```bash
# Clone the repo
cd gmail-mcp-project

# Create and activate a virtual environment
python -m venv .venv

# Activate (run this line only, not both):
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows PowerShell

# Install Python dependencies
pip install -e .
```

> **Windows note:** Run each command separately, one at a time. Do not paste all lines at once.

---

## 3. Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...           # your Anthropic API key
GOOGLE_CLIENT_SECRET=client_secret.json  # path to the file you downloaded in step 1
```

The other variables (`TOKEN_DB_PATH`, `AGENT_LOG_FILE`) have sensible defaults and don't need to be changed.

---

## 4. Running — CLI (core)

```bash
python agent/cli.py
```

On first run a browser window will open asking you to authorize the app. Sign in with the Gmail account you added as a test user. After authorizing, the token is saved to `tokens.db` and the browser tab can be closed.

Subsequent runs will use the stored token and refresh it automatically.

### Example prompts

```
You: Summarize my unread emails from this week
You: Show me the last 5 emails from notifications@github.com
You: Draft a reply to the last email from andy@example.com saying I'm in for Thursday
You: List my Gmail labels
```

The agent runs a full multi-turn loop: it may call several tools in sequence before replying (e.g. search → get_thread → create_draft).

### Watching tool calls live

Every turn is logged to `agent_log.jsonl` **and** streamed to stdout. Each line is a JSON object with a `type` field:

| type | description |
|------|-------------|
| `user_turn` | what you typed |
| `assistant_text` | the model's text reply |
| `tool_use` | a tool the model invoked (name + input) |
| `tool_result` | what the tool returned |

To tail the log in a separate terminal:

```bash
tail -f agent_log.jsonl | python -m json.tool
```

---

## 5. Running — Web UI (stretch)

The web UI requires two terminals running simultaneously.

**Terminal 1 — Python backend (FastAPI):**
```bash
python -m backend.main
```

**Terminal 2 — Next.js frontend:**
```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:3000` in your browser.

### Web UI flow
1. Click **Connect Gmail** — redirects to Google's consent screen
2. After authorizing, you're redirected back to the chat UI
3. Type a message or click a suggestion button
4. Responses stream in as the agent works, with tool activity shown inline
5. Click **Disconnect** to revoke the session

---

## 6. Validate the MCP server standalone (optional)

Before wiring the agent you can test the server directly with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python gmail_mcp/server.py
```

This opens a web UI at `http://localhost:5173` where you can list and invoke each tool interactively.

---

## Expired token handling

When a refresh token expires (after 7 days in Testing mode), the next run will:

1. Detect the `invalid_grant` error from Google's token endpoint
2. Print a clear warning explaining why
3. Delete the stale token from `tokens.db`
4. Re-open the browser for a fresh consent flow (CLI) or prompt reconnect (web UI)

No crash, no manual cleanup needed. Just re-authorize when prompted.

---

## Project structure

```
gmail-mcp-project/
├── agent/
│   └── cli.py                  # CLI chat agent + MCP client wiring
├── auth/
│   ├── token_store.py          # OAuth flow for CLI (installed-app)
│   └── token_store_web.py      # OAuth flow for web (server-side code exchange)
├── backend/
│   ├── main.py                 # FastAPI server — auth + streaming chat endpoints
│   └── agent_stream.py         # Streaming agent for web backend
├── gmail_mcp/
│   ├── server.py               # MCP server (stdio) — tool definitions + handlers
│   └── gmail_client.py         # Thin Gmail REST API wrapper
├── frontend/
│   ├── src/app/
│   │   ├── page.tsx            # Chat UI with Connect Gmail button
│   │   ├── page.module.css     # Component styles
│   │   ├── layout.tsx          # Next.js layout
│   │   └── globals.css         # Global styles + CSS variables
│   ├── next.config.js          # Proxies /api/* to FastAPI on port 8000
│   └── package.json
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## Design notes

**Tool schema design** — Each tool has a precise description written for the model, not just for humans. Required vs optional fields are explicit. Error responses always include an `"error"` key so the model can reason about failures.

**Agent loop** — The loop continues calling tools until `stop_reason != "tool_use"`, handling multi-step reasoning (e.g. search → read thread → draft reply) within a single user turn. Full message history is passed on every request.

**Auth** — Credentials never touch the Anthropic API. The OAuth flow runs locally. `invalid_grant` is caught specifically (not swallowing all `RefreshError`s) so auth bugs don't silently pass. The CLI uses the installed-app flow; the web backend uses the Authorization Code flow with the state parameter preserved across the redirect to handle PKCE correctly.

**No send tool** — Intentional. Only `create_draft` is exposed.

**Streaming** — The web backend yields SSE chunks as the agent processes each token and tool call, so the UI updates in real time rather than waiting for the full response.

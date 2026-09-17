# MCP AI Assistant

A production-ready FastAPI backend that powers an AI assistant using the
Model Context Protocol (MCP). Tools (web search, file operations, your
internal APIs, anything else) are **not hardcoded** — they're discovered at
startup from whatever MCP servers are listed in [`mcp_config.json`](mcp_config.json),
and handed to Gemini as a single unified tool list. Add a server to the
config and the agent can use it; no orchestration code changes.

## How it works

```
Browser UI  --(HTTPS, SSE)-->  FastAPI backend  --(Gemini API)-->  Gemini
                                      |
                                      +--(MCP stdio/http)--> web_search server
                                      +--(MCP stdio/http)--> filesystem server
                                      +--(MCP stdio/http)--> internal_api server (yours)
```

1. On startup, `MCPManager` (`app/mcp/manager.py`) connects to every server
   in `mcp_config.json`, calls `list_tools()` on each, and merges the
   results into one registry (`server__toolname` -> schema). MCP's
   `inputSchema` is plain JSON Schema, so it's passed straight into Gemini's
   `FunctionDeclaration.parameters_json_schema` with no conversion step.
2. `POST /api/chat/stream` (`app/api/routes.py`) accepts `{message, conversation_id?}`
   and streams Server-Sent Events back. Conversation history is loaded from the
   database, so the client only ever sends the new message.
3. `Agent.run_stream` (`app/agent/orchestrator.py`) calls Gemini with the
   full tool list. When Gemini requests a function call, the manager
   dispatches it to the right MCP server, feeds the result back to Gemini,
   and repeats until Gemini gives a final answer — all while streaming text
   tokens to the client as they're generated.

Note: `google-genai` also has *experimental* built-in MCP support (you can
hand it a raw `mcp.ClientSession` and it auto-calls tools for you). This
project drives the tool loop manually instead, because that's what lets
`MCPManager` unify multiple MCP servers into one registry and lets the
backend emit `tool_call`/`tool_result` SSE events the UI can render as
live "using a tool..." indicators — the automatic path hides those steps.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`:
- `GEMINI_API_KEY` — from aistudio.google.com/apikey
- `BACKEND_API_KEY` — any long random string; your frontend sends this back as a bearer token
- `TAVILY_API_KEY` — from tavily.com (free tier available) if you keep the web_search server enabled
- `MODEL` — check ai.google.dev/gemini-api/docs/models for the current Gemini lineup and pick your cost/quality tradeoff (defaults to `gemini-2.5-flash`)

You also need Node.js installed locally (for the `npx`-launched filesystem
MCP server referenced in `mcp_config.json`).

Run it:

```bash
uvicorn app.main:app --reload
```

Check `GET http://localhost:8000/api/health` — it lists which MCP servers
connected and how many tools were discovered.

## Configuring tools (`mcp_config.json`)

Each entry is either a `stdio` server (a local process, e.g. `npx ...` or
`python -m ...`) or an `http` server (a remote MCP endpoint). `${VAR}` is
expanded from environment variables at startup, so secrets never live in
the JSON file itself.

- **web_search** — points at Tavily's hosted MCP endpoint. Swap for Brave
  Search, Exa, or any other search MCP server by changing this one block.
- **filesystem** — the official `@modelcontextprotocol/server-filesystem`,
  sandboxed to `/app/data`. On Render this is ephemeral container storage;
  swap for an S3/GCS-backed MCP server if you need persistence.
- **internal_api** — `app/mcp_servers/internal_api_server.py`, a small
  `FastMCP` server you own. Replace `get_user_profile` / `create_support_ticket`
  with real calls into your product's backend — every `@mcp.tool()` function
  is auto-discovered, so that's the only file you need to touch to add a
  new internal capability.

Disable any server you don't need yet by setting `"enabled": false`.

## Connecting your frontend UI

Call this backend **from your frontend's server/edge layer**, not directly
from client-side JS — `BACKEND_API_KEY` is a shared secret and would be
readable in the browser otherwise. If your UI is Next.js, add a small route
handler that forwards to this backend and streams the response through.

The endpoint is a POST that streams SSE, so `EventSource` (GET-only) won't
work directly — read the fetch stream yourself:

```js
const res = await fetch("https://your-backend.onrender.com/api/chat/stream", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${BACKEND_API_KEY}`,
  },
  body: JSON.stringify({ message: userInput, history: priorMessages }),
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const chunks = buffer.split("\n\n");
  buffer = chunks.pop(); // keep the trailing partial chunk
  for (const chunk of chunks) {
    const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"));
    if (!dataLine) continue;
    const event = JSON.parse(dataLine.slice(5));
    // event.type: "text" | "tool_call" | "tool_result" | "done" | "error"
    if (event.type === "text") appendToAssistantMessage(event.text);
    if (event.type === "tool_call") showToolIndicator(event.name, event.input);
  }
}
```

(Or use `@microsoft/fetch-event-source`, which handles POST+SSE parsing and
reconnects for you.)

## Deploying to Render

1. Push this repo to GitHub.
2. In Render: New -> Blueprint -> point at the repo (`render.yaml` is
   already set up for a Docker web service).
3. Set the secret env vars Render prompts for: `GEMINI_API_KEY`,
   `BACKEND_API_KEY`, `TAVILY_API_KEY`.
4. Set `ALLOWED_ORIGINS` to your deployed frontend's origin (only needed if
   the browser calls this backend directly — leave blank if you're proxying
   through your frontend server).
5. Deploy. Use the **Starter** plan (not Free) for production — Render's
   free web services spin down on idle, which means a ~30s cold start (and
   a dropped MCP subprocess connection) on the first request after a lull.

## Costs

- **`google-genai` SDK / MCP protocol**: free, open-source.
- **Gemini API**: pay-per-token, no subscription. Check current pricing at
  ai.google.dev/gemini-api/docs/pricing for the model set in `MODEL` — Flash
  and Flash-Lite tiers are the cheapest, Pro is the most capable.
- **Tavily** (web search): free tier (~1,000 searches/month as of writing),
  paid tiers beyond that.
- **Render**: Starter web service is ~$7/month; Free tier works for demos
  but sleeps on idle.

## Database

SQLite locally (no setup — a file appears at `./agent.db`), Postgres on Render
(wired automatically by `render.yaml`). Same code both places; only
`DATABASE_URL` changes. Render hands out `postgres://` URLs, which
`app/config.py` rewrites to the async driver SQLAlchemy needs.

Tables are created on startup by `init_db()`. Once real data exists and the
schema starts changing, switch to Alembic migrations instead.

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chat/stream` | Send a message, stream the reply |
| `GET` | `/api/conversations` | List threads, newest activity first |
| `GET` | `/api/conversations/{id}` | Full message history for one thread |
| `DELETE` | `/api/conversations/{id}` | Delete a thread and its messages |

Start a new thread by omitting `conversation_id`; the stream's first event
returns the new id, which the client sends on every following message.

## Production hardening ideas (not included, scoped out for v1)

- Per-user auth (JWT) instead of one shared `BACKEND_API_KEY`, plus a `user_id`
  column on conversations so people only see their own threads. **Right now
  every conversation is visible to anyone with the API key.**
- Persist tool calls alongside messages if the UI should show "used web search"
  badges when reloading an old thread.
- Rate limiting (e.g. `slowapi`) to cap per-user request/token spend.
- Structured logging + tracing around tool calls for debugging/observability.
- Retry/backoff around MCP server reconnects if a stdio subprocess dies mid-run.

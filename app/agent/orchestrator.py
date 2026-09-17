"""The agent loop: send the conversation + the full MCP tool registry to
Gemini, stream text back as it's generated, and whenever Gemini asks to call
a function, dispatch it to the right MCP server and feed the result back in
-- repeating until Gemini produces a final answer with no more function calls.

Note: google-genai has experimental built-in MCP support (you can hand it a
raw mcp.ClientSession and it will auto-discover + auto-call tools). We don't
use that here on purpose: MCPManager already gives us one unified registry
across *all* configured MCP servers (not just one session), and driving the
tool loop ourselves is what lets us stream "tool_call"/"tool_result" events
to the frontend UI as they happen.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.mcp.manager import MCPManager

logger = logging.getLogger("agent")

SYSTEM_PROMPT = (
    "You are a helpful AI assistant embedded in a web product. You have "
    "access to a set of tools discovered dynamically from MCP servers "
    "(web search, file operations, internal APIs, etc.). Use a tool "
    "whenever it would make your answer more accurate or current instead "
    "of guessing. Be concise and direct.\n\n"
    "File paths are always relative to the file server's own sandbox root, "
    "so use bare names like 'notes.txt' rather than repeating the sandbox "
    "folder name in the path. Call list_allowed_directories if unsure."
)

MAX_TOOL_ROUNDS = 8
MAX_RETRIES = 4


class Agent:
    def __init__(self, client: genai.Client, mcp_manager: MCPManager, model: str, max_tokens: int):
        self._client = client
        self._mcp = mcp_manager
        self._model = model
        self._max_tokens = max_tokens

    def _build_tools(self) -> list[types.Tool] | None:
        defs = self._mcp.tool_defs()
        if not defs:
            return None
        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters_json_schema=t["input_schema"],
            )
            for t in defs
        ]
        return [types.Tool(function_declarations=declarations)]

    async def _open_stream(self, contents: list[types.Content], config: types.GenerateContentConfig):
        """Yield model chunks, retrying transient overload/rate-limit errors.

        503s and 429s are common and usually clear within seconds. The SDK
        defers the HTTP request until the stream is first iterated, so the
        error surfaces there and not from the call itself — retrying around
        the call alone never fires. Pull the first chunk inside the retry
        instead. That is still safe: nothing has reached the client yet.
        Once tokens are flowing a failure propagates, since replaying would
        duplicate text the user has already seen.
        """
        delay = 1.0
        for attempt in range(MAX_RETRIES):
            try:
                stream = await self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                )
                iterator = stream.__aiter__()
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except genai_errors.APIError as exc:
                if exc.code not in (429, 503) or attempt == MAX_RETRIES - 1:
                    raise
                logger.warning("Gemini %s, retrying in %.0fs", exc.code, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue

            yield first
            async for chunk in iterator:
                yield chunk
            return

    async def run_stream(self, messages: list[dict[str, Any]]) -> AsyncGenerator[dict, None]:
        """`messages` is [{"role": "user"|"assistant", "content": "..."}].

        Yields plain-dict events ready to be serialized as SSE:
        {"type": "text", "text": "..."}
        {"type": "tool_call", "name": "...", "input": {...}}
        {"type": "tool_result", "name": "...", "output": "..."}
        {"type": "done"}
        {"type": "error", "message": "..."}
        """
        contents: list[types.Content] = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]
        tools = self._build_tools()
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=tools,
            max_output_tokens=self._max_tokens,
        )

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                # Keep the Part objects exactly as received. Gemini 3 attaches a
                # thought_signature to function-call parts and rejects the next
                # request if it isn't echoed back verbatim, so these must never
                # be rebuilt from their contents.
                received_parts: list[types.Part] = []
                function_call_parts: list[types.Part] = []

                async for chunk in self._open_stream(contents, config):
                    if not chunk.candidates:
                        continue
                    parts = chunk.candidates[0].content.parts or []
                    for part in parts:
                        received_parts.append(part)
                        if part.text:
                            yield {"type": "text", "text": part.text}
                        if part.function_call:
                            function_call_parts.append(part)

                # Gemini intermittently returns a STOP with no parts at all.
                # Without this the user just sees an empty reply bubble.
                if not received_parts:
                    yield {
                        "type": "error",
                        "message": "The model returned an empty response. Please try again.",
                    }
                    return

                contents.append(types.Content(role="model", parts=received_parts))

                if not function_call_parts:
                    yield {"type": "done"}
                    return

                response_parts: list[types.Part] = []
                for part in function_call_parts:
                    fc = part.function_call
                    yield {"type": "tool_call", "name": fc.name, "input": dict(fc.args or {})}
                    output = await self._mcp.call_tool(fc.name, dict(fc.args or {}))
                    yield {"type": "tool_result", "name": fc.name, "output": output}
                    response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": output},
                        )
                    )

                contents.append(types.Content(role="user", parts=response_parts))

            yield {"type": "error", "message": "Stopped after too many tool-use rounds."}
        except genai_errors.APIError as exc:
            logger.exception("Gemini API error")
            if exc.code == 429:
                message = (
                    "Rate limit reached for this model. Wait a minute and try again, "
                    "or switch MODEL in your .env to a different Gemini model."
                )
            else:
                message = f"Model error ({exc.code}): {exc.message}"
            yield {"type": "error", "message": message}

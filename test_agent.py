"""A tiny terminal chat client for testing the backend.

This does exactly what your website frontend will do: send a message, then read
the streamed events back one at a time. Reading this file is the fastest way to
understand the contract your UI has to implement.

How to run it:
  1. Start the backend in one terminal:  uvicorn app.main:app --reload
  2. Run this in a SECOND terminal:      python test_agent.py
"""
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

URL = "http://127.0.0.1:8000/api/chat/stream"
API_KEY = os.environ["BACKEND_API_KEY"]


def send(message: str, conversation_id: str | None) -> str | None:
    """Send one message, print the reply as it streams, return the conversation id."""
    payload = {"message": message, "conversation_id": conversation_id}
    headers = {"Authorization": f"Bearer {API_KEY}"}

    with httpx.stream("POST", URL, json=payload, headers=headers, timeout=180) as response:
        if response.status_code != 200:
            response.read()
            print(f"\n[HTTP {response.status_code}] {response.text}")
            return conversation_id

        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:"):])

            if event["type"] == "conversation":
                conversation_id = event["conversation_id"]
            elif event["type"] == "text":
                print(event["text"], end="", flush=True)
            elif event["type"] == "tool_call":
                print(f"\n  [calling tool] {event['name']}({event['input']})", flush=True)
            elif event["type"] == "tool_result":
                preview = event["output"][:150].replace("\n", " ")
                print(f"  [tool replied] {preview}...\n", flush=True)
            elif event["type"] == "error":
                print(f"\n  [ERROR] {event['message']}")
            elif event["type"] == "done":
                print()

    return conversation_id


def main() -> None:
    print("Chat with your agent. Type 'quit' to exit.")
    print("History is saved server-side, so restarting this script keeps the thread.\n")
    conversation_id: str | None = None

    while True:
        message = input("you > ").strip()
        if message.lower() in {"quit", "exit"}:
            break
        if not message:
            continue

        print("bot > ", end="", flush=True)
        conversation_id = send(message, conversation_id)


if __name__ == "__main__":
    main()

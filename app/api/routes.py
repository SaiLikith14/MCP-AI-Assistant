import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from app.agent.orchestrator import Agent
from app.api.auth import require_api_key
from app.db import repository
from app.models.schemas import ChatRequest, ConversationOut, MessageOut

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    mcp_manager = request.app.state.mcp_manager
    return {
        "status": "ok",
        "servers": sorted({t.server_name for t in mcp_manager.tools.values()}),
        "tool_count": len(mcp_manager.tools),
    }


@router.get("/tools", dependencies=[Depends(require_api_key)])
async def list_tools(request: Request):
    mcp_manager = request.app.state.mcp_manager
    return {"tools": mcp_manager.tool_defs()}


@router.get(
    "/conversations",
    response_model=list[ConversationOut],
    dependencies=[Depends(require_api_key)],
)
async def list_conversations():
    return await repository.list_conversations()


@router.get(
    "/conversations/{conversation_id}",
    response_model=list[MessageOut],
    dependencies=[Depends(require_api_key)],
)
async def get_conversation(conversation_id: str):
    if not await repository.conversation_exists(conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return await repository.get_messages(conversation_id)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_key)],
)
async def delete_conversation(conversation_id: str):
    if not await repository.conversation_exists(conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    await repository.delete_conversation(conversation_id)


@router.post("/chat/stream", dependencies=[Depends(require_api_key)])
async def chat_stream(payload: ChatRequest, request: Request):
    mcp_manager = request.app.state.mcp_manager
    genai_client = request.app.state.genai_client
    settings = request.app.state.settings

    if payload.conversation_id:
        if not await repository.conversation_exists(payload.conversation_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        conversation_id = payload.conversation_id
    else:
        conversation = await repository.create_conversation(payload.message)
        conversation_id = conversation.id

    history = await repository.get_history(conversation_id)
    await repository.add_message(conversation_id, "user", payload.message)

    agent = Agent(
        client=genai_client,
        mcp_manager=mcp_manager,
        model=settings.model,
        max_tokens=settings.max_tokens,
    )
    messages = [*history, {"role": "user", "content": payload.message}]

    async def event_generator():
        # Sent first so a new conversation's id reaches the client immediately.
        yield {
            "event": "conversation",
            "data": json.dumps({"type": "conversation", "conversation_id": conversation_id}),
        }

        reply: list[str] = []
        async for event in agent.run_stream(messages):
            if event["type"] == "text":
                reply.append(event["text"])
            yield {"event": event["type"], "data": json.dumps(event)}

        answer = "".join(reply).strip()
        if answer:
            await repository.add_message(conversation_id, "assistant", answer)

    return EventSourceResponse(event_generator())

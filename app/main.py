import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google import genai

from app.api.routes import router
from app.config import get_settings
from app.db.session import init_db
from app.mcp.manager import MCPManager

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.genai_client = genai.Client(api_key=settings.gemini_api_key)

    await init_db()

    mcp_manager = MCPManager(settings.mcp_config_path)
    await mcp_manager.connect_all()
    app.state.mcp_manager = mcp_manager

    yield

    await mcp_manager.close()


app = FastAPI(title="MCP AI Assistant", lifespan=lifespan)

settings = get_settings()
if settings.allowed_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router, prefix="/api")

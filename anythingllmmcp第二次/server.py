import asyncio
import os

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

ANYLLM_BASE_URL = os.environ.get("ANYLLM_BASE_URL", "http://localhost:3001/api/v1")
ANYLLM_API_KEY = os.environ.get("ANYLLM_API_KEY", "")
ANYLLM_WORKSPACE_SLUG = os.environ.get("ANYLLM_WORKSPACE_SLUG", "")

mcp = MCPServer("anythingllm", version="0.1.0")


def _headers() -> dict:
    return {"Authorization": f"Bearer {ANYLLM_API_KEY}"}


_slug = None


def _resolve_slug() -> str:
    global _slug
    if ANYLLM_WORKSPACE_SLUG:
        return ANYLLM_WORKSPACE_SLUG
    if _slug is None:
        resp = httpx.get(f"{ANYLLM_BASE_URL}/workspaces", headers=_headers(), timeout=30)
        resp.raise_for_status()
        workspaces = resp.json()["workspaces"]
        if not workspaces:
            raise RuntimeError("No workspaces found in AnythingLLM")
        _slug = workspaces[0]["slug"]
    return _slug


@mcp.tool()
async def ask_workspace(question: str) -> dict:
    """Ask the AnythingLLM workspace a question and return the AI answer with its sources."""
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{ANYLLM_BASE_URL}/workspace/{_resolve_slug()}/chat",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"message": question, "mode": "chat"},
        )
        if resp.status_code == 403:
            raise RuntimeError("AnythingLLM rejected the API key (403). Check ANYLLM_API_KEY.")
        resp.raise_for_status()
        data = resp.json()
    return {
        "answer": data.get("textResponse", ""),
        "sources": [s.get("title", "") for s in (data.get("sources") or [])],
    }


if __name__ == "__main__":
    asyncio.run(
        mcp.run_streamable_http_async(
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
            stateless_http=True,
            json_response=True,
        )
    )
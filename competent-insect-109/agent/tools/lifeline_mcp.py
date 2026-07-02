import os
from pydantic_ai.mcp import MCPToolset
from dotenv import load_dotenv

load_dotenv()


def get_lifeline_toolset() -> MCPToolset:
    """
    Initializes the connection to the unified Lifeline MCP Server.
    This single connection discovers all 5 tools:
    - search_shelters
    - lock_shelter_capacity
    - search_volunteers
    - dispatch_volunteer
    - evaluate_physical_compatibility
    """
    mcp_url = os.getenv("LIFELINE_MCP_URL")
    if not mcp_url:
        raise ValueError("FATAL: LIFELINE_MCP_URL is not set in .env")

    return MCPToolset(mcp_url)
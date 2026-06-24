from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset


def get_weather_toolset():
    """Connect to Open-Meteo weather MCP server via stdio."""
    return MCPToolset(
        StdioTransport(command="python", args=["-m", "openmeteo_mcp_server"])
    )
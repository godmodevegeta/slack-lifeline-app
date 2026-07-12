import os
import sys
from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset


def get_weather_toolset():
    """Connect to Open-Meteo weather MCP server via stdio."""
    # Use sys.prefix to find the venv bin (works regardless of VIRTUAL_ENV)
    venv_bin = os.path.join(sys.prefix, "bin")
    weather_server = os.path.join(venv_bin, "weather-server")
    return MCPToolset(
        StdioTransport(command=weather_server, args=[])
    )
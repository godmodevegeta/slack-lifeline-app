from agent.tools.lifeline_mcp import get_lifeline_toolset
from agent.tools.weather import get_weather_toolset


def get_all_toolsets():
    """
    Returns a list of all initialized MCP toolsets for the Agent.
    Order matters: Lifeline first (core), then Weather (demo).
    """
    toolsets = [
        get_lifeline_toolset(),
        get_weather_toolset(),
    ]
    return toolsets
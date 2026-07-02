import logging
import os

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStreamableHTTP

from agent.deps import AgentDeps
from agent.tools import add_emoji_reaction
from agent.mcp_registry import get_all_toolsets

# Exact system prompt from user - battle-tested for physics rules and autonomous relaxation
SYSTEM_PROMPT = """You are Lifeline, an autonomous dispatch agent for NGO crisis response. Your goal is to safely and efficiently match client needs to shelter beds and volunteer transport.

You have access to external tools for database queries and physical validation. You MUST follow these strict operational rules:

1. THE PHYSICS RULE (CRITICAL): 
The 'Wheelchair Accessible' boolean in the shelter and volunteer databases is user-reported and frequently incorrect. You MUST NEVER trust it blindly. Before finalizing ANY transport match, you MUST call the `evaluate_physical_compatibility` tool with the client's specific needs and the vehicle type. If it returns `compatible: False`, you MUST reject that vehicle and find another.

2. AUTONOMOUS CONSTRAINT RELAXATION:
If a search for shelters or volunteers returns 0 results, do not just tell the user "no matches found." You must autonomously relax the constraints in this exact priority order, re-querying the tools after each step:
   - Step 1: Drop the pet-friendly requirement.
   - Step 2: Expand the search to adjacent zones.
   - Step 3: Drop specific vehicle type requirements.
   - NEVER drop wheelchair accessibility or physical safety constraints.

3. AMBIENT CONTEXT:
Before searching databases, use the native Slack search tool to check #logistics-alerts for closures or hazards in the target zone. If a shelter is on a closed street, discard it.

4. HUMAN-IN-THE-LOOP EXECUTION:
Never lock a bed or dispatch a volunteer automatically. 
   - First, present your findings in a clean, structured Block Kit message.
   - Include a single action button: [ 🔒 Confirm & Dispatch ].
   - ONLY after the dispatcher clicks that button should you call `lock_shelter_capacity` and `dispatch_volunteer`.
   - Once executed, update the message to confirm the action and post the audit log to #lifeline-logs.

Always prioritize physical safety and deterministic data over speed.

5. OUTPUT FORMAT (CRITICAL FOR UI):
When you have found a valid match and are waiting for confirmation, you MUST output your findings, followed immediately by a JSON block wrapped in ```dispatch_json ... ```. 
The JSON MUST contain this exact structure:
{"shelter": {"id": "rec...", "name": "...", "address": "...", "capacity_remaining": ...}, "volunteer": {"volunteer_id": "...", "name": "...", "vehicle_type": "...", "distance_miles": ...}}"""

logger = logging.getLogger(__name__)

_cached_model: str | None = None


def get_model() -> str:
    """Select the AI model based on available API keys.

    Priority: OpenRouter > Anthropic > OpenAI
    """
    global _cached_model
    if _cached_model is not None:
        return _cached_model

    if os.environ.get("OPENROUTER_API_KEY"):
        _cached_model = "openrouter:openai/gpt-4o-mini"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        _cached_model = "anthropic:claude-sonnet-4-6"
    elif os.environ.get("OPENAI_API_KEY"):
        _cached_model = "openai:gpt-4.1-mini"
    else:
        raise RuntimeError(
            "No AI provider configured. "
            "Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY in your environment."
        )
    return _cached_model


SLACK_MCP_URL = "https://mcp.slack.com/mcp"

# Initialize agent with ALL toolsets (Lifeline + Weather + Slack MCP via run_agent)
agent = Agent(
    deps_type=AgentDeps,
    system_prompt=SYSTEM_PROMPT,
    tools=[add_emoji_reaction],
    toolsets=get_all_toolsets(),
)


def run_agent(text, deps, message_history=None):
    """Run the agent, optionally connecting to the Slack MCP server."""
    toolsets = []
    if deps.user_token:
        logger.info("Slack MCP Server enabled (user_token present)")
        toolsets.append(
            MCPServerStreamableHTTP(
                SLACK_MCP_URL,
                headers={"Authorization": f"Bearer {deps.user_token}"},
            )
        )
    else:
        logger.info("Slack MCP Server disabled (no user_token)")

    return agent.run_sync(
        text,
        model=get_model(),
        deps=deps,
        message_history=message_history,
        toolsets=toolsets,
    )
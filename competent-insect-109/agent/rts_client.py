import logging
from slack_sdk import WebClient

logger = logging.getLogger(__name__)


async def sweep_ambient_context(
    client: WebClient,
    channel_ids: list[str],
    query: str,
    timeframe: str = "2h",
) -> str:
    """
    Use Slack's assistant.search.context (RTS API) to fetch ambient alerts
    from logistics channels before dispatch.
    
    Returns formatted string of relevant alerts or empty string.
    """
    try:
        # Note: assistant.search.context requires user_token with assistant:read
        # Falls back gracefully if not available
        result = await client.assistant_search_context(
            channel_ids=channel_ids,
            query=query,
            timeframe=timeframe,
            count=5,
        )
        
        messages = result.get("messages", {}).get("matches", [])
        if not messages:
            return ""
            
        alerts = []
        for msg in messages:
            text = msg.get("text", "")
            channel = msg.get("channel", {}).get("name", "unknown")
            alerts.append(f"[#{channel}] {text}")
            
        return "\n".join(f"⚠️ Ambient Alert: {a}" for a in alerts)
        
    except Exception as e:
        logger.warning(f"RTS sweep failed: {e}")
        return ""


def build_rts_query(zone: str) -> str:
    """Build RTS query for a specific zone."""
    return f"({zone}) AND (closure OR outage OR full OR hazard OR blocked)"
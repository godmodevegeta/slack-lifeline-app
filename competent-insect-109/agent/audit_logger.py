import logging
from slack_sdk import WebClient

logger = logging.getLogger(__name__)


async def post_audit_log(
    client: WebClient,
    logs_channel_id: str,
    audit_data: dict,
) -> bool:
    """
    Post structured audit log to #lifeline-logs channel.
    Expects audit_data dict with keys from MCP tool returns + dispatch metadata.
    """
    try:
        # Format structured log message
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📋 LIFELINE AUDIT LOG", "emoji": True}
            },
            {"type": "divider"},
        ]
        
        # Add fields from audit_data
        for key, value in audit_data.items():
            if value:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{key}:*\n```{value}```"}
                })
        
        await client.chat_postMessage(
            channel=logs_channel_id,
            blocks=blocks,
            text="Lifeline audit log",
        )
        return True
        
    except Exception as e:
        logger.error(f"Failed to post audit log: {e}")
        return False


def format_dispatch_audit(
    shelter: dict,
    volunteer: dict,
    dispatcher_id: str,
    shelter_lock_result: dict,
    dispatch_result: dict,
) -> dict:
    """Format dispatch results into audit log structure."""
    return {
        "dispatcher": dispatcher_id,
        "shelter": f"{shelter.get('name')} ({shelter.get('id')}) - {shelter_lock_result.get('message')}",
        "volunteer": f"{volunteer.get('name')} ({volunteer.get('volunteer_id')}) - {dispatch_result.get('message')}",
        "shelter_audit": shelter_lock_result.get("audit_log", "N/A"),
        "volunteer_audit": dispatch_result.get("audit_log", "N/A"),
    }
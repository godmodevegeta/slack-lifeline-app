import logging
from slack_sdk import WebClient

logger = logging.getLogger(__name__)


def _format_rejected_candidates(candidates: list) -> str:
    """Format rejected candidates into a readable string for the audit log."""
    if not candidates:
        return "None"
    parts = []
    for c in candidates:
        name = c.get("name", "Unknown")
        vehicle = c.get("vehicle_type", "N/A")
        reason = c.get("reason", "unknown")
        parts.append(f"{name} ({vehicle}) — {reason}")
    return "; ".join(parts)


def post_audit_log(
    client: WebClient,
    logs_channel_id: str,
    audit_data: dict,
) -> bool:
    """Post a dense, structured audit log to #lifeline-logs.

    Layout:
      1. Header: timestamped title
      2. Traceability: clickable permalink to original #intake message
      3. Metadata block: dispatcher, shelter (capacity transition), volunteer
      4. System state: ambient context + rejected candidates
      5. Raw payload: developer debug JSON at the bottom
    """
    try:
        timestamp = audit_data.get("timestamp", "unknown time")
        permalink = audit_data.get("permalink", "")
        dispatcher_id = audit_data.get("dispatcher_id", "unknown")
        shelter_name = audit_data.get("shelter_name", "Unknown")
        shelter_id = audit_data.get("shelter_id", "N/A")
        original_cap = audit_data.get("original_capacity", "?")
        new_cap = audit_data.get("new_capacity", "?")
        volunteer_name = audit_data.get("volunteer_name", "Unknown")
        volunteer_id = audit_data.get("volunteer_id", "N/A")
        ambient_alert = audit_data.get("ambient_alert", "")
        rejected = audit_data.get("rejected_candidates", [])

        capacity_str = f"{original_cap} → {new_cap}" if new_cap != "?" else str(original_cap)

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📋 DISPATCH AUDIT: {timestamp}",
                    "emoji": True,
                },
            },
        ]

        if permalink:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔗 <{permalink}|Original request in #intake>",
                },
            })

                # ── Richer Block Kit Layout ──
        
        # 1. Core Entities (Side-by-Side using 'fields')
        blocks.extend([
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*🏠 Shelter*\n{shelter_name}\nCapacity: {capacity_str}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*🚐 Transport*\n{volunteer_name}\nStatus: Dispatched"
                    }
                ]
            },
            # 2. Traceability & IDs (Using 'context' for dense, secondary metadata)
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Dispatcher: <@{dispatcher_id}> • Shelter ID: `{shelter_id}` • Volunteer ID: `{volunteer_id}`"
                    }
                ]
            },
            {"type": "divider"},
        ])

        # 3. System State & Rejections (Formatted as a clean, scannable bulleted list)
        rejected_text = "None"
        if rejected:
            # Format: • *Name (Vehicle)*: Reason
            rejected_items = [
                f"• *{c.get('name', 'Unknown')}* ({c.get('vehicle_type', 'N/A')}): {c.get('reason', 'unknown')}" 
                for c in rejected
            ]
            rejected_text = "\n".join(rejected_items)
            
        ambient_text = ambient_alert if ambient_alert else "None detected"
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*🧠 System State*\n"
                    f"*Ambient Context:* {ambient_text}\n\n"
                    f"*Rejected Candidates:*\n{rejected_text}"
                )
            }
        })

        # if agent_output:
        #     truncated = agent_output[:2900] if len(agent_output) > 2900 else agent_output
        #     blocks.extend([
        #         {"type": "divider"},
        #         {
        #             "type": "section",
        #             "text": {
        #                 "type": "mrkdwn",
        #                 "text": f"Raw dispatch payload:\n{truncated}\n",
        #             },
        #         },
        #     ])

        client.chat_postMessage(
            channel=logs_channel_id,
            blocks=blocks,
            text=f"Dispatch audit: {shelter_name} / {volunteer_name}",
        )
        return True

    except Exception as e:
        logger.error(f"Failed to post audit log: {e}")
        return False


def format_dispatch_audit(
    dispatcher_id: str,
    shelter_name: str,
    shelter_id: str,
    original_capacity,
    new_capacity,
    volunteer_name: str,
    volunteer_id: str,
    ambient_alert: str = "",
    rejected_candidates: list = None,
    agent_output: str = "",
    permalink: str = "",
    timestamp: str = "",
) -> dict:
    """Format dispatch results into the audit log data structure."""
    return {
        "dispatcher_id": dispatcher_id,
        "shelter_name": shelter_name,
        "shelter_id": shelter_id,
        "original_capacity": original_capacity,
        "new_capacity": new_capacity,
        "volunteer_name": volunteer_name,
        "volunteer_id": volunteer_id,
        "ambient_alert": ambient_alert,
        "rejected_candidates": rejected_candidates or [],
        "agent_output": agent_output,
        "permalink": permalink,
        "timestamp": timestamp,
    }

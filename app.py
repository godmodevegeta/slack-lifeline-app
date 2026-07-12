import os
import random
import logging
import json
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Initializes your app with your bot token
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
LOGISTICS_CHANNEL_ID = "C0BD08GS800" 

# ==============================================================================
#  LOGGER CONFIGURATION
# ==============================================================================
# Set up a clean, readable log format
logger = logging.getLogger("LifelineAgent")
logger.setLevel(logging.INFO) # Change to logging.DEBUG to see raw API payloads

# Create console handler with a specific format
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
ch.setFormatter(formatter)
logger.addHandler(ch)

# ==============================================================================
# MODULE 1: THE NEURO SWEEP (Automated RTS API Integration)
# ==============================================================================

@app.event("app_mention")
def handle_lifeline_dispatch(event, client, say, context):
    """
    Listens for @Lifeline mentions. Automatically triggers the RTS API 
    to scan #logistics-alerts for ambient context.
    """
    channel_id = event["channel"]
    user_id = event["user"]
    
    logger.info(f"🚨 TRIGGERED: Received @mention from user {user_id} in channel {channel_id}")
    
    # 🚨 CRITICAL FIX: Extract the Team ID from the event payload.
    team_id = event.get("team") 
    logger.info(f"🔑 Extracted team_id from payload: {team_id}")
    
    # 1. Immediate UI Feedback
    say(
        text="🔄 **Lifeline is assessing the situation...**\n• Parsing intake details...\n• Scanning `#logistics-alerts` for ambient context...",
        thread_ts=event["ts"]
    )

    # 2. Extract the action_token
    action_token = event.get("action_token") or context.get("action_token")
    logger.info(f"🎟️ Action token present: {bool(action_token)}")
    
    if not action_token:
        logger.error("❌ FATAL: action_token missing from event payload. RTS API will fail.")
        say(text="❌ **Error:** Missing `action_token`. Check Slack Bolt SDK version.", thread_ts=event["ts"])
        return

    # 3. Execute Real-Time Search (RTS) API
    try:
        rts_query = "blocked"
        logger.info(f"🔍 Executing RTS API. Query: '{rts_query}' | Target Channel: {LOGISTICS_CHANNEL_ID}")
        
        api_params = {
            "token": context.bot_token,
            "action_token": action_token, 
            "team_id": team_id, 
            "query": rts_query,
            "channel_types": ["public_channel"],
            "content_types": ["messages"],
            "include_bots": False,
            "sort": "timestamp",
            "sort_dir": "desc",
            "limit": 10 
        }
        
        # Debug: Log params without exposing tokens
        safe_params = {k: v for k, v in api_params.items() if "token" not in k}
        logger.debug(f"📡 RTS API Params: {json.dumps(safe_params, indent=2)}")
        
        rts_response = client.api_call(
            api_method="assistant.search.context",
            params=api_params
        )

        logger.info(f"📥 RTS API Response OK: {rts_response.get('ok')}")

        if rts_response["ok"]:
            results = rts_response.get("results", {})
            messages = results.get("messages", [])
            logger.info(f"📊 Total raw messages returned by Slack: {len(messages)}")
            
            # Debug: Inspect the structure of the first message to ensure we are looking at the right keys
            if messages:
                logger.debug(f"🔬 Keys in first message payload: {list(messages[0].keys())}")
            
            # Filter results to ONLY include messages from #logistics-alerts
            # Note: Slack API sometimes nests channel info. We check both root and nested.
            filtered_messages = [
                msg for msg in messages 
                if msg.get("channel_id") == LOGISTICS_CHANNEL_ID or 
                   msg.get("channel", {}).get("id") == LOGISTICS_CHANNEL_ID
            ]
            
            logger.info(f"🎯 Messages matched to #logistics-alerts: {len(filtered_messages)}")
            
            if filtered_messages:
                # 4. Synthesize and format the ambient context
                alerts_text = ""
                for hit in filtered_messages:
                    # Handle both 'content' and 'text' keys just in case
                    msg_text = hit.get("content", hit.get("text", "No text found")) 
                    permalink = hit.get("permalink", "#")
                    alerts_text += f"• <{permalink}|[Alert]> {msg_text}\n"
                
                logger.info("✅ Ambient context successfully synthesized and sent to Slack.")
                say(
                    text=f"⚠️ **AMBIENT CONTEXT DETECTED:**\n{alerts_text}\n\n_🧠 Now verifying physical constraints via Ontology MCP..._",
                    thread_ts=event["ts"]
                )
                
            else:
                logger.info("✅ Sweep clear. No matching logistics alerts found.")
                say(
                    text="✅ **Ambient Sweep Clear:** No active logistics alerts in `#logistics-alerts`.\n\n_🧠 Now verifying physical constraints via Ontology MCP..._",
                    thread_ts=event["ts"]
                )
        else:
            error_msg = rts_response.get("error", "unknown error")
            logger.error(f"❌ RTS API returned an error: {error_msg}")
            say(text=f"❌ RTS API Error: {error_msg}", thread_ts=event["ts"])

    except Exception as e:
        logger.exception("💥 Unhandled exception during RTS Sweep:")
        say(text=f"⚠️ System Error during RTS Sweep: {str(e)}", thread_ts=event["ts"])


# ==============================================================================
# BOILERPLATE / TESTING
# ==============================================================================

@app.event("message")
def handle_message_events(body, logger):
    """Catch-all to prevent 'Unhandled request' warnings in the console."""
    pass

@app.message("hello")
def message_hello(message, say) -> None:
    say(text=f"Hey there <@{message['user']}>! (Lifeline System Online)")

@app.action("button_click")
def action_button_click(body, ack, say) -> None:
    ack()
    say(f"<@{body['user']['id']}> clicked the dispatch confirmation button.")

# Start your app
if __name__ == "__main__":
    logger.info("🚀 Starting Lifeline Agent via Socket Mode...")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


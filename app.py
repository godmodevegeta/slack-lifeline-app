import os
import random
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Initializes your app with your bot token
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# ==============================================================================
# MODULE 1: THE NEURO SWEEP (Automated RTS API Integration)
# ==============================================================================

@app.event("app_mention")
def handle_lifeline_dispatch(event, client, say, context):
    """
    Listens for @Lifeline mentions in any channel (e.g., #intake).
    Automatically triggers the RTS API to scan #logistics-alerts for ambient context.
    """
    channel_id = event["channel"]
    user_id = event["user"]
    
    # 1. Immediate UI Feedback (The "Thinking" State)
    say(
        text="🔄 **Lifeline is assessing the situation...**\n• Parsing intake details...\n• Scanning `#logistics-alerts` for ambient context...",
        thread_ts=event["ts"] # Reply in a thread to keep #intake clean
    )

    # 2. Execute Real-Time Search (RTS) API
    # We use search.messages with channel-specific syntax to isolate #logistics-alerts
    try:
        rts_query = "channel:#logistics-alerts (closure OR flood OR closed OR outage OR blocked)"
        
        # Call the native Slack RTS API
        rts_response = client.search_messages(
            query=rts_query,
            sort="timestamp",
            sort_dir="desc",
            count=3 # We only need the 3 most recent alerts
        )

        if rts_response["ok"]:
            matches = rts_response["messages"]["matches"]
            
            if matches:
                # 3. Synthesize and format the ambient context
                alerts_text = ""
                for hit in matches:
                    channel_name = hit["channel"]["name"]
                    msg_text = hit["text"]
                    permalink = hit["permalink"]
                    alerts_text += f"• <{permalink}|[{channel_name}]> {msg_text}\n"
                
                say(
                    text=f"⚠️ **AMBIENT CONTEXT DETECTED:**\n{alerts_text}\n\n_🧠 Now verifying physical constraints via Ontology MCP..._",
                    thread_ts=event["ts"]
                )
                # NEXT STEP: Pass 'matches' to the Ontology MCP logic here
                
            else:
                say(
                    text="✅ **Ambient Sweep Clear:** No active logistics alerts in `#logistics-alerts`.\n\n_🧠 Now verifying physical constraints via Ontology MCP..._",
                    thread_ts=event["ts"]
                )
                # NEXT STEP: Proceed to MCP tool calls
        else:
            say(text=f"❌ RTS API Error: {rts_response.get('error')}", thread_ts=event["ts"])

    except Exception as e:
        say(text=f"⚠️ System Error during RTS Sweep: {str(e)}", thread_ts=event["ts"])


# ==============================================================================
# BOILERPLATE / TESTING (Keep your existing handlers for local testing)
# ==============================================================================

@app.message("hello")
def message_hello(message, say) -> None:
    say(text=f"Hey there <@{message['user']}>! (Lifeline System Online)")

@app.action("button_click")
def action_button_click(body, ack, say) -> None:
    ack()
    say(f"<@{body['user']['id']}> clicked the dispatch confirmation button.")

# Start your app
if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
import os
import random
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Initializes your app with your bot token
app: App = App(token=os.environ.get("SLACK_BOT_TOKEN"))


# Listens to incoming messages that contain "hello"
@app.message("hello")
def message_hello(message, say) -> None:
    say(
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"Hey there <@{message['user']}>!"}
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Click Me"},
                        "action_id": "button_click",
                        "accessibility_label": "sup bro. comon clickkkk!!"
                    }
                ]
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "_sup bro. comon clickkkk!!_"}
                ]
            }
        ],                                                                                           
        text=f"Hey there <@{message['user']}>!",
    )


@app.message(r"^search:\s*(.*)")
def handle_realtime_search(context, client, say, message) -> None:
    """
    Handles messages like "search: project alpha" and returns search results
    from the workspace using the assistant.search.context API.
    """
    # Extract the search query from the regex group
    search_query: str = context["matches"][0]

    if not search_query.strip():
        say("Please provide a search query. Example: `search: project alpha`")
        return

    say(f"🔍 Searching workspace for: `{search_query}`...")

    try:
        # Prepare the API call parameters
        api_params = {
            "query": search_query,
            "limit": 5,                       # max 20 per page
            "channel_types": ["public_channel", "private_channel"],
            "content_types": ["messages"],
            "include_bots": False,
        }

        # ⚠️ IMPORTANT: action_token handling
        #
        # If you use a **bot token** (xoxb-), you MUST provide an action_token
        # that Slack passes in the event payload. This token is needed for
        # assistant.search.context to work with bot tokens.
        #
        # If you use a **user token** (xoxp-), you do NOT need action_token.
        #
        # The code below tries to get action_token from the context (if available).
        # Make sure your app subscribes to `message.channels` or `app_mention`
        # events so that action_token is present.
        if "action_token" in context:
            api_params["action_token"] = context["action_token"]
        else:
            # If using a bot token and no action_token, the call will fail.
            # You can log a warning or fall back to a user token if you have one.
            print("Warning: No action_token found. If using a bot token, search will fail.")

        # Call the API using the generic api_call method
        # (If your SDK has client.assistant.search.context, you can use that instead)
        response = client.api_call(
            api_method="assistant.search.context",
            params=api_params
        )

        # Handle the response
        if response.get("ok"):
            results = response.get("results", {})
            messages = results.get("messages", [])

            if not messages:
                say("❌ No matching messages found.")
                return

            # Build a nice Slack message with blocks
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*🔎 Search results for: '{search_query}'*"}
                },
                {"type": "divider"}
            ]

            for msg in messages[:5]:   # Show at most 5
                author = msg.get("author_name", "Unknown user")
                channel_name = msg.get("channel_name", "unknown")
                channel_id = msg.get("channel_id")
                content = msg.get("content", "")
                permalink = msg.get("permalink", "#")
                timestamp = msg.get("timestamp")

                # Format timestamp (optional)
                time_str = ""
                if timestamp:
                    try:
                        dt = datetime.fromtimestamp(float(timestamp))
                        time_str = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"👤 *{author}* in <#{channel_id}|{channel_name}>:\n"
                            f"> {content}\n"
                            f"🕒 {time_str}  ·  <{permalink}|View original message>"
                        )
                    }
                })

            # Show pagination hint if more results exist
            next_cursor = response.get("next_cursor")
            if next_cursor:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*More results available.* Use `search: {search_query} --page` to fetch next page."}
                    ]
                })

            say(blocks=blocks, text=f"Search results for '{search_query}'")

        else:
            error = response.get("error", "unknown error")
            say(f"❌ Search failed: {error}")

    except Exception as e:
        say(f"⚠️ An error occurred: {str(e)}")

# Listens to incoming message that contains "goodbye"
@app.message("goodbye")
def message_goodbye(say) -> None:
    responses: list[str] = ["Adios", "Au revoir", "Fairwell"]
    parting: str = random.choice(responses)
    say(f"{parting}!")


@app.action("button_click")
def action_button_click(body, ack, say) -> None:
    ack()
    say(f"<@{body['user']['id']}> clicked the button")


# Start your app
if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()




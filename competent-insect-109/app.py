import logging
import os

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from agent import get_model
from listeners import register_listeners

load_dotenv(dotenv_path=".env", override=False)
get_model()  # Fail fast if no AI provider key is configured

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-8s | %(message)s', datefmt='%H:%M:%S'))
logging.basicConfig(level=logging.INFO, handlers=[handler])

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    # client=WebClient(
    #     base_url=os.environ.get("SLACK_API_URL", "https://slack.com/api"),
    #     token=os.environ.get("SLACK_BOT_TOKEN"),
    # ),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"), # Required for HTTP mode

)

register_listeners(app)

if __name__ == "__main__":
    SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN")).start()

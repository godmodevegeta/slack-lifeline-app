from slack_bolt import App

from .feedback_buttons import handle_feedback_button
from .confirm_dispatch import handle_confirm_dispatch
from listeners.views.dispatch_card import DISPATCH_BUTTON_ACTION_ID


def register(app: App):
    app.action("feedback")(handle_feedback_button)
    app.action(DISPATCH_BUTTON_ACTION_ID)(handle_confirm_dispatch)

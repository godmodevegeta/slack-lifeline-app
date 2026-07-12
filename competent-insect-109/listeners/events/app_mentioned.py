import json
import os
import re
from logging import Logger

from slack_bolt import BoltContext, Say, SayStream, SetStatus
from slack_sdk import WebClient

from agent import AgentDeps, run_agent
from thread_context import conversation_store
from listeners.views.dispatch_card import (
    build_pending_card,
    build_partial_match_card,
    build_no_match_blocks,
)
from listeners.views.feedback_builder import build_feedback_blocks

DISPATCH_JSON_RE = re.compile(r"```dispatch_json\s*(\{.*?\})\s*```", re.DOTALL)
PARTIAL_MATCH_JSON_RE = re.compile(r"```partial_match_json\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_dispatch_json(text: str) -> dict | None:
    """Extract the first ```dispatch_json``` block from the agent output."""
    match = DISPATCH_JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_partial_match_json(text: str) -> dict | None:
    """Extract the first ```partial_match_json``` block from the agent output."""
    match = PARTIAL_MATCH_JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _strip_json_blocks(text: str) -> str:
    """Remove all dispatch_json and partial_match_json blocks from the text."""
    text = DISPATCH_JSON_RE.sub("", text)
    text = PARTIAL_MATCH_JSON_RE.sub("", text)
    return text.strip()


def handle_app_mentioned(
    client: WebClient,
    context: BoltContext,
    event: dict,
    logger: Logger,
    say: Say,
    say_stream: SayStream,
    set_status: SetStatus,
):
    """Handle @mentions in channels.

    In the #intake channel this invokes the Lifeline agent (which autonomously
    gathers ambient context via its native Slack MCP search tools) and posts a
    Block Kit confirmation card when a dispatch match is found. In other
    channels it falls back to the standard conversational flow.
    """
    try:
        channel_id = context.channel_id
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]
        user_id = context.user_id

        cleaned_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if not cleaned_text:
            say(
                text="Hey there! How can I help you? Ask me anything and I'll do my best.",
                thread_ts=thread_ts,
            )
            return

        set_status(
            status="Thinking...",
            loading_messages=[
                "Scanning logistics alerts…",
                "Querying shelter inventory…",
                "Matching volunteer transport…",
                "Validating physical compatibility…",
                "Assembling dispatch plan…",
            ],
        )

        history = conversation_store.get_history(channel_id, thread_ts)

        deps = AgentDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=event["ts"],
            user_token=context.user_token or os.environ.get("SLACK_USER_TOKEN"),
        )

        is_intake = channel_id == deps.intake_channel_id

        result = run_agent(cleaned_text, deps, message_history=history)

        dispatch_data = _extract_dispatch_json(result.output)
        partial_match_data = _extract_partial_match_json(result.output)
        findings_text = _strip_json_blocks(result.output)

        if is_intake and dispatch_data is not None:
            streamer = say_stream()
            streamer.append(markdown_text=":mag: Match found. Building dispatch card...")
            streamer.stop(blocks=build_pending_card(dispatch_data, findings_text))
            if findings_text:
                say(text=findings_text, thread_ts=thread_ts)
        elif is_intake and partial_match_data is not None:
            streamer = say_stream()
            streamer.append(markdown_text=":mag: Partial match found. Building card...")
            streamer.stop(blocks=build_partial_match_card(partial_match_data, findings_text))
            if findings_text:
                say(text=findings_text, thread_ts=thread_ts)
        elif is_intake:
            streamer = say_stream()
            streamer.append(markdown_text=":mag: Search complete. Building summary...")
            streamer.stop(blocks=build_no_match_blocks(findings_text))
            if findings_text:
                say(text=findings_text, thread_ts=thread_ts)
        else:
            streamer = say_stream()
            streamer.append(markdown_text=result.output)
            streamer.stop(blocks=build_feedback_blocks())

        conversation_store.set_history(channel_id, thread_ts, result.all_messages())

    except Exception as e:
        logger.exception(f"Failed to handle app mention: {e}")
        say(
            text=f":warning: Something went wrong! ({e})",
            thread_ts=event.get("thread_ts") or event["ts"],
        )

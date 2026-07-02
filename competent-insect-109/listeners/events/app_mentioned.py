import asyncio
import json
import re
from logging import Logger

from slack_bolt import BoltContext, Say, SayStream, SetStatus
from slack_sdk import WebClient

from agent import AgentDeps, run_agent
from agent.rts_client import sweep_ambient_context, build_rts_query
from thread_context import conversation_store
from listeners.views.dispatch_card import (
    build_dispatch_card,
    build_no_match_blocks,
)
from listeners.views.feedback_builder import build_feedback_blocks

DISPATCH_JSON_RE = re.compile(r"```dispatch_json\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_dispatch_json(text: str) -> dict | None:
    """Extract the first ```dispatch_json``` block from the agent output."""
    match = DISPATCH_JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _split_findings_and_json(text: str) -> tuple[str, dict | None]:
    """Split agent output into narrative findings and parsed dispatch JSON."""
    dispatch_data = _extract_dispatch_json(text)
    if dispatch_data is None:
        return text.strip(), None
    findings = DISPATCH_JSON_RE.sub("", text).strip()
    return findings, dispatch_data


def _perform_rts_sweep(
    client: WebClient,
    logs_channel_id: str,
    text: str,
    user_token: str | None,
) -> str:
    """Run the RTS sweep synchronously, returning ambient alerts (or empty)."""
    try:
        return asyncio.run(
            sweep_ambient_context(
                client=client,
                channel_ids=[logs_channel_id],
                query=build_rts_query("zone"),
            )
        )
    except RuntimeError:
        return ""


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

    In the #intake channel this performs an RTS sweep, invokes the Lifeline
    agent, and posts a Block Kit confirmation card when a dispatch match is
    found. In other channels it falls back to the standard conversational flow.
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
            user_token=context.user_token,
        )

        # Intake channel: trigger RTS sweep before agent invocation
        is_intake = channel_id == deps.intake_channel_id
        if is_intake:
            ambient = _perform_rts_sweep(
                client=client,
                logs_channel_id=deps.logs_channel_id,
                text=cleaned_text,
                user_token=context.user_token,
            )
            if ambient:
                deps.ambient_alerts = ambient

        result = run_agent(cleaned_text, deps, message_history=history)

        findings_text, dispatch_data = _split_findings_and_json(result.output)

        if is_intake and dispatch_data is not None:
            streamer = say_stream()
            streamer.append(markdown_text=findings_text)
            streamer.stop(blocks=build_dispatch_card(dispatch_data, findings_text))
        elif is_intake:
            streamer = say_stream()
            streamer.append(markdown_text=findings_text)
            streamer.stop(blocks=build_no_match_blocks(findings_text))
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

import json
import os
import re
from datetime import datetime
from logging import Logger

from slack_bolt import Ack, BoltContext, Respond
from slack_sdk import WebClient

from agent import AgentDeps, run_agent
from agent.audit_logger import post_audit_log, format_dispatch_audit
from listeners.views.dispatch_card import (
    build_processing_card,
    build_confirmed_card,
)

EXECUTION_JSON_RE = re.compile(r"```execution_json\s*(\{.*?\})\s*```", re.DOTALL)
DISPATCH_JSON_RE = re.compile(r"```dispatch_json\s*(\{.*?\})\s*```", re.DOTALL)
NEW_CAPACITY_RE = re.compile(r'"new_capacity"\s*:\s*(\d+)', re.DOTALL)


def _parse_button_value(value: str) -> dict:
    """Decode the JSON-encoded button value into a dispatch payload dict."""
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_execution_json(text: str) -> dict:
    """Extract execution results from the agent's output.

    Tries (in order):
      1. ```execution_json``` block (preferred)
      2. ```dispatch_json``` block that contains tool results (agent used wrong format)
      3. Regex for "new_capacity": N in the raw text (last resort)
    """
    # 1. Try execution_json first
    match = EXECUTION_JSON_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. Fallback: try dispatch_json that contains tool results
    match = DISPATCH_JSON_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(1))
            if "shelter_lock_result" in data:
                return data
        except json.JSONDecodeError:
            pass

    # 3. Last resort: regex for new_capacity in the raw text
    cap_match = NEW_CAPACITY_RE.search(text)
    if cap_match:
        return {"shelter_lock_result": {"new_capacity": int(cap_match.group(1))}}

    return {}


def _reconstruct_nested_payload(dispatch_payload: dict) -> dict:
    """Reconstruct a nested dispatch_data dict from the flat button value.

    The button value uses flat keys (shelter_id, shelter_name, ...) but the
    card builders expect a nested structure ({"shelter": {...}, "volunteer": {...}}).
    """
    return {
        "client_need": dispatch_payload.get("client_need", ""),
        "shelter": {
            "id": dispatch_payload.get("shelter_id"),
            "name": dispatch_payload.get("shelter_name"),
            "address": dispatch_payload.get("shelter_address"),
            "capacity_remaining": dispatch_payload.get("shelter_capacity_remaining"),
            "image": dispatch_payload.get("shelter_image"),
            "phone": dispatch_payload.get("shelter_phone"),
            "meals_provided": dispatch_payload.get("shelter_meals"),
        },
        "volunteer": {
            "volunteer_id": dispatch_payload.get("volunteer_id"),
            "name": dispatch_payload.get("volunteer_name"),
            "vehicle_type": dispatch_payload.get("volunteer_vehicle_type"),
            "distance_miles": dispatch_payload.get("volunteer_distance_miles"),
            "eta_minutes": dispatch_payload.get("volunteer_eta_minutes"),
            "image": dispatch_payload.get("volunteer_image"),
            "languages": dispatch_payload.get("volunteer_languages"),
        },
        "ambient_alert": dispatch_payload.get("ambient_alert", ""),
        "rejected_candidates": dispatch_payload.get("rejected_candidates", []),
        "beds_to_lock": dispatch_payload.get("beds_to_lock", 1),
    }


def _update_message(
    client: WebClient,
    channel_id: str,
    message_ts: str,
    blocks: list,
    text: str = "",
) -> None:
    """Replace the original card with a new state."""
    client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=text or "Dispatch update",
        blocks=blocks,
    )


def handle_confirm_dispatch(
    ack: Ack,
    body: dict,
    client: WebClient,
    context: BoltContext,
    logger: Logger,
    respond: Respond,
):
    """Handle the [ 🔒 Confirm & Dispatch ] button click.

    Three-phase state transition on the original card:
      1. Pending → Processing (instant UI feedback, button disappears)
      2. Agent executes lock_shelter_capacity + dispatch_volunteer
      3. Processing → Confirmed (green banner, timestamp)
    Then posts a dense audit log to #lifeline-logs.
    """
    ack()

    try:
        action = body["actions"][0]
        dispatch_payload = _parse_button_value(action.get("value", ""))

        shelter_id = dispatch_payload.get("shelter_id")
        volunteer_id = dispatch_payload.get("volunteer_id")
        shelter_name = dispatch_payload.get("shelter_name", "Unknown shelter")
        volunteer_name = dispatch_payload.get("volunteer_name", "Unknown volunteer")
        beds_to_lock = dispatch_payload.get("beds_to_lock", 1)

        if not shelter_id or not volunteer_id:
            respond(text=":warning: Missing shelter or volunteer ID. Cannot dispatch.")
            return

        channel_id = context.channel_id
        message_ts = body["message"]["ts"]
        user_id = context.user_id

        # ── Phase 1: Instant processing card (button disappears) ──
        nested_dispatch_data = _reconstruct_nested_payload(dispatch_payload)
        _update_message(
            client,
            channel_id,
            message_ts,
            build_processing_card(nested_dispatch_data),
            text="Processing dispatch...",
        )

        # ── Phase 2: Agent execution ──
        execution_prompt = (
            "Execute the confirmed dispatch now. Call these tools in order:\n"
            f"1. `lock_shelter_capacity` with shelter_id={shelter_id!r}, "
            f"beds_to_lock={beds_to_lock!r}, initiated_by_user={user_id!r}\n"
            f"2. `dispatch_volunteer` with volunteer_id={volunteer_id!r}, "
            f"shelter_name={shelter_name!r}, initiated_by_user={user_id!r}\n"
            "Do not search for alternatives. The dispatcher has confirmed this match.\n"
            "IMPORTANT: Do NOT output dispatch_json. After both tool calls complete, "
            "output ONLY a JSON block wrapped in ```execution_json ... ``` with this "
            "exact structure:\n"
            '{"shelter_lock_result": {"status": "...", "new_capacity": ...}, '
            '"dispatch_result": {"status": "..."}}'
        )

        deps = AgentDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=message_ts,
            message_ts=message_ts,
            user_token=context.user_token or os.environ.get("SLACK_USER_TOKEN"),
            dispatch_id=f"{shelter_id}:{volunteer_id}",
            matched_shelter=dispatch_payload,
            matched_volunteer=dispatch_payload,
        )

        result = run_agent(execution_prompt, deps)
        agent_output = result.output if hasattr(result, "output") else str(result)
        execution_data = _extract_execution_json(agent_output)

        new_capacity = (
            execution_data.get("shelter_lock_result", {}).get("new_capacity")
        )

        logger.info(
            f"Dispatch execution complete: shelter={shelter_id} "
            f"volunteer={volunteer_id} dispatcher={user_id} new_capacity={new_capacity}"
        )

        # ── Phase 3: Confirmed card + audit log ──
        timestamp_str = datetime.now().strftime("%I:%M %p")
        _update_message(
            client,
            channel_id,
            message_ts,
            build_confirmed_card(nested_dispatch_data, timestamp_str),
            text=f"Dispatch confirmed at {timestamp_str}",
        )

        # Get permalink to the original #intake message for audit traceability
        permalink = ""
        try:
            link_result = client.chat_getPermalink(
                channel=channel_id, message_ts=message_ts
            )
            if link_result.get("ok"):
                permalink = link_result.get("permalink", "")
        except Exception as link_err:
            logger.warning(f"Failed to get permalink: {link_err}")

        # Post dense audit log to #lifeline-logs
        audit_data = format_dispatch_audit(
            dispatcher_id=user_id,
            shelter_name=shelter_name,
            shelter_id=shelter_id,
            original_capacity=dispatch_payload.get("shelter_capacity_remaining"),
            new_capacity=new_capacity,
            volunteer_name=volunteer_name,
            volunteer_id=volunteer_id,
            ambient_alert=dispatch_payload.get("ambient_alert", ""),
            rejected_candidates=dispatch_payload.get("rejected_candidates", []),
            agent_output=agent_output,
            permalink=permalink,
            timestamp=timestamp_str,
        )
        try:
            post_audit_log(
                client=client,
                logs_channel_id=deps.logs_channel_id,
                audit_data=audit_data,
            )
        except Exception as audit_err:
            logger.error(f"Audit log failed: {audit_err}")

    except Exception as e:
        logger.exception(f"Failed to handle confirm dispatch: {e}")
        respond(text=f":warning: Dispatch failed: {e}")

import json
from logging import Logger

from slack_bolt import Ack, BoltContext, Respond
from slack_sdk import WebClient

from agent import AgentDeps, run_agent
from agent.audit_logger import post_audit_log, format_dispatch_audit
from listeners.views.dispatch_card import build_dispatched_blocks


def _parse_button_value(value: str) -> dict:
    """Decode the JSON-encoded button value into a dispatch payload dict."""
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def _update_message(
    client: WebClient,
    channel_id: str,
    message_ts: str,
    blocks: list,
) -> None:
    """Replace the original confirmation card with the dispatched card."""
    client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text="Dispatch confirmed",
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

    Invokes the agent to execute `lock_shelter_capacity` and
    `dispatch_volunteer` for the encoded shelter/volunteer IDs, then updates
    the original card and posts an audit log.
    """
    ack()

    try:
        action = body["actions"][0]
        dispatch_payload = _parse_button_value(action.get("value", ""))

        shelter_id = dispatch_payload.get("shelter_id")
        volunteer_id = dispatch_payload.get("volunteer_id")
        shelter_name = dispatch_payload.get("shelter_name", "Unknown shelter")
        volunteer_name = dispatch_payload.get("volunteer_name", "Unknown volunteer")

        if not shelter_id or not volunteer_id:
            respond(text=":warning: Missing shelter or volunteer ID. Cannot dispatch.")
            return

        channel_id = context.channel_id
        message_ts = body["message"]["ts"]
        user_id = context.user_id

        # Build a secondary agent prompt that drives the MCP tool execution.
        execution_prompt = (
            "Execute the confirmed dispatch now. You MUST call the following tools "
            "in order, then report their results:\n"
            f"1. `lock_shelter_capacity` with shelter_id={shelter_id!r}\n"
            f"2. `dispatch_volunteer` with volunteer_id={volunteer_id!r}\n"
            "Do not search for alternatives. The dispatcher has confirmed this match. "
            "Return the raw tool results."
        )

        deps = AgentDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=message_ts,
            message_ts=message_ts,
            user_token=context.user_token,
            dispatch_id=f"{shelter_id}:{volunteer_id}",
            matched_shelter=dispatch_payload,
            matched_volunteer=dispatch_payload,
        )

        result = run_agent(execution_prompt, deps)
        agent_output = result.output if hasattr(result, "output") else str(result)

        logger.info(
            f"Dispatch execution complete: shelter={shelter_id} "
            f"volunteer={volunteer_id} dispatcher={user_id}"
        )

        # Update the original confirmation card to the dispatched state.
        _update_message(
            client,
            channel_id,
            message_ts,
            build_dispatched_blocks(shelter_name, volunteer_name),
        )

        # Post the agent's tool result as a thread reply for traceability.
        respond(text=f":memo: Tool execution result:\n```{agent_output}```")

        # Post structured audit log to #lifeline-logs.
        audit_data = format_dispatch_audit(
            shelter={
                "id": shelter_id,
                "name": shelter_name,
                "address": dispatch_payload.get("shelter_address"),
            },
            volunteer={
                "volunteer_id": volunteer_id,
                "name": volunteer_name,
                "vehicle_type": dispatch_payload.get("volunteer_vehicle_type"),
            },
            dispatcher_id=user_id,
            shelter_lock_result={"message": "locked", "audit_log": agent_output},
            dispatch_result={"message": "dispatched", "audit_log": agent_output},
        )
        try:
            import asyncio

            asyncio.run(
                post_audit_log(
                    client=client,
                    logs_channel_id=deps.logs_channel_id,
                    audit_data=audit_data,
                )
            )
        except Exception as audit_err:
            logger.error(f"Audit log failed: {audit_err}")

    except Exception as e:
        logger.exception(f"Failed to handle confirm dispatch: {e}")
        respond(text=f":warning: Dispatch failed: {e}")

import json

from slack_sdk.models.blocks import (
    Block,
    ButtonElement,
    ContextBlock,
    DividerBlock,
    HeaderBlock,
    MarkdownTextObject,
    PlainTextObject,
    SectionBlock,
)

DISPATCH_BUTTON_ACTION_ID = "confirm_dispatch"


def _extract_zone(findings_text: str) -> str:
    """Best-effort extraction of a zone token from the agent's narrative."""
    for token in findings_text.split():
        token = token.strip(".,;:!?()[]")
        if token.lower().startswith("zone"):
            return token
    return ""


def build_dispatch_card(dispatch_data: dict, findings_text: str = "") -> list[Block]:
    """Build the Block Kit confirmation card for a dispatch match.

    Args:
        dispatch_data: Parsed JSON from the agent's ```dispatch_json``` block.
        findings_text: The agent's narrative findings (placed in a section).

    Returns:
        List of Block Kit blocks to post to Slack.
    """
    shelter = dispatch_data.get("shelter", {}) or {}
    volunteer = dispatch_data.get("volunteer", {}) or {}

    blocks: list[Block] = [
        HeaderBlock(
            text=PlainTextObject(text=":rotating_light: Dispatch Match Found", emoji=True)
        ),
        DividerBlock(),
    ]

    if findings_text:
        blocks.append(
            SectionBlock(text=MarkdownTextObject(text=findings_text.strip()))
        )

    blocks.append(
        SectionBlock(
            text=MarkdownTextObject(
                text=(
                    f"*:house: Shelter*\n"
                    f"*{shelter.get('name', 'Unknown')}*\n"
                    f"{shelter.get('address', 'No address on file')}\n"
                    f"Beds remaining: `{shelter.get('capacity_remaining', 'N/A')}`"
                )
            )
        )
    )
    blocks.append(
        ContextBlock(
            elements=[
                MarkdownTextObject(text=f"Shelter ID: `{shelter.get('id', 'N/A')}`")
            ]
        )
    )
    blocks.append(
        SectionBlock(
            text=MarkdownTextObject(
                text=(
                    f"*:car: Volunteer Transport*\n"
                    f"*{volunteer.get('name', 'Unknown')}*\n"
                    f"Vehicle: `{volunteer.get('vehicle_type', 'N/A')}`\n"
                    f"Distance: `{volunteer.get('distance_miles', 'N/A')} miles`"
                )
            )
        )
    )
    blocks.append(
        ContextBlock(
            elements=[
                MarkdownTextObject(
                    text=f"Volunteer ID: `{volunteer.get('volunteer_id', 'N/A')}`"
                )
            ]
        )
    )

    button_value = json.dumps(
        {
            "shelter_id": shelter.get("id"),
            "shelter_name": shelter.get("name"),
            "shelter_address": shelter.get("address"),
            "shelter_capacity_remaining": shelter.get("capacity_remaining"),
            "volunteer_id": volunteer.get("volunteer_id"),
            "volunteer_name": volunteer.get("name"),
            "volunteer_vehicle_type": volunteer.get("vehicle_type"),
            "volunteer_distance_miles": volunteer.get("distance_miles"),
            "zone": _extract_zone(findings_text),
        }
    )

    blocks.append(DividerBlock())
    blocks.append(
        SectionBlock(
            block_id="dispatch_actions",
            text=MarkdownTextObject(
                text="Review the match above. Confirm to lock the bed and dispatch the volunteer."
            ),
            accessory=ButtonElement(
                action_id=DISPATCH_BUTTON_ACTION_ID,
                text=PlainTextObject(text="🔒 Confirm & Dispatch", emoji=True),
                style="primary",
                value=button_value,
            ),
        )
    )
    return blocks


def build_no_match_blocks(reason: str = "") -> list[Block]:
    """Build a card to post when no valid dispatch match was found."""
    text = (
        reason.strip()
        if reason.strip()
        else "No valid shelter + volunteer match found after constraint relaxation."
    )
    return [
        HeaderBlock(
            text=PlainTextObject(text=":x: No Dispatch Match", emoji=True)
        ),
        DividerBlock(),
        SectionBlock(text=MarkdownTextObject(text=text)),
    ]


def build_dispatched_blocks(
    shelter_name: str, volunteer_name: str
) -> list[Block]:
    """Build the post-confirmation card showing successful dispatch."""
    return [
        HeaderBlock(
            text=PlainTextObject(text=":white_check_mark: Dispatch Confirmed", emoji=True)
        ),
        DividerBlock(),
        SectionBlock(
            text=MarkdownTextObject(
                text=(
                    f"Bed locked at *{shelter_name}*.\n"
                    f"Volunteer *{volunteer_name}* dispatched."
                )
            )
        ),
        ContextBlock(
            elements=[
                MarkdownTextObject(text="Audit log posted to #lifeline-logs.")
            ]
        ),
    ]

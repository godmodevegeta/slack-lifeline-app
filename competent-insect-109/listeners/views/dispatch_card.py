import json

from slack_sdk.models.blocks import (
    Block,
    ButtonElement,
    ContextBlock,
    DividerBlock,
    HeaderBlock,
    ImageElement,
    MarkdownTextObject,
    PlainTextObject,
    SectionBlock,
)

DISPATCH_BUTTON_ACTION_ID = "confirm_dispatch"


def _format_address(address) -> str:
    """Normalize an Airtable address field (list or string) for display."""
    if isinstance(address, list):
        return ", ".join(str(part) for part in address if part)
    return str(address) if address else "No address on file"


def _format_meals(meals) -> str:
    """Format meals_provided into a readable string."""
    if not meals:
        return "N/A"
    if isinstance(meals, list):
        return ", ".join(str(m) for m in meals)
    return str(meals)


def _build_shelter_section(shelter: dict) -> SectionBlock:
    """Build the shelter section with image accessory."""
    name = shelter.get("name", "Unknown")
    address = _format_address(shelter.get("address"))
    cap = shelter.get("capacity_remaining", "N/A")
    phone = shelter.get("phone", "N/A")
    meals = _format_meals(shelter.get("meals_provided"))
    image_url = shelter.get("image", "")

    text = (
        f":house: *Shelter*\n"
        f"*{name}*\n"
        f"{address}\n"
        f"`{cap}` beds remaining\n"
        f":phone: {phone} • :fork_and_knife: {meals}"
    )

    return SectionBlock(
        text=MarkdownTextObject(text=text),
        accessory=ImageElement(
            image_url=image_url,
            alt_text=f"Photo of {name}",
        ),
    )


def _build_volunteer_section(volunteer: dict, show_eta: bool = True) -> SectionBlock:
    """Build the volunteer section with image accessory."""
    name = volunteer.get("name", "Unknown")
    vehicle = volunteer.get("vehicle_type", "N/A")
    distance = volunteer.get("distance_miles", "N/A")
    eta = volunteer.get("eta_minutes", "N/A")
    languages = volunteer.get("languages", "N/A")
    image_url = volunteer.get("image", "")

    if show_eta:
        status_line = f":car: {vehicle} • {distance} mi • ETA {eta} min"
    else:
        status_line = f":car: {vehicle} • Status: Dispatched"

    text = (
        f":car: *Transport*\n"
        f"*{name}*\n"
        f"{status_line}\n"
        f":speech_balloon: {languages}"
    )

    return SectionBlock(
        text=MarkdownTextObject(text=text),
        accessory=ImageElement(
            image_url=image_url,
            alt_text=f"Photo of {name}",
        ),
    )


def _build_routing_logic(dispatch_data: dict) -> str:
    """Build the 'Routing Logic' narrative from rejected_candidates."""
    volunteer = dispatch_data.get("volunteer", {}) or {}
    vol_name = volunteer.get("name", "Unknown")
    vol_vehicle = volunteer.get("vehicle_type", "N/A")
    rejected = dispatch_data.get("rejected_candidates") or []

    if not rejected:
        return (
            f":brain: *Routing Logic*\n"
            f"Matched {vol_name} ({vol_vehicle}). "
            f"All candidates passed physical validation."
        )

    lines = [":brain: *Routing Logic*", f"Matched {vol_name} ({vol_vehicle})."]
    for r in rejected:
        rname = r.get("name", "Unknown")
        rvehicle = r.get("vehicle_type", "N/A")
        rreason = r.get("reason", "unknown reason")
        lines.append(f"Rejected {rname} ({rvehicle}) — {rreason}.")
    return "\n".join(lines)


def build_pending_card(dispatch_data: dict, findings_text: str = "") -> list[Block]:
    """State 1: Pending — match found, awaiting dispatcher confirmation.

    findings_text is intentionally unused — the card is 100% structured from JSON.
    The agent's narrative is posted as a thread reply by the listener.
    """
    shelter = dispatch_data.get("shelter", {}) or {}
    volunteer = dispatch_data.get("volunteer", {}) or {}
    client_need = dispatch_data.get("client_need", "")

    blocks: list[Block] = [
        HeaderBlock(
            text=PlainTextObject(
                text=":rotating_light: MATCH FOUND — AWAITING CONFIRMATION",
                emoji=True,
            )
        ),
        DividerBlock(),
    ]

    if client_need:
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(
                    text=f":bust_in_silhouette: *Client Need*\n{client_need}"
                )
            )
        )
        blocks.append(DividerBlock())

    blocks.append(_build_shelter_section(shelter))
    blocks.append(_build_volunteer_section(volunteer))
    blocks.append(
        ContextBlock(
            elements=[
                MarkdownTextObject(
                    text=(
                        f"Shelter ID: `{shelter.get('id', 'N/A')}`"
                        f" • Volunteer ID: `{volunteer.get('volunteer_id', 'N/A')}`"
                    )
                )
            ]
        )
    )
    blocks.append(DividerBlock())

    blocks.append(
        SectionBlock(text=MarkdownTextObject(text=_build_routing_logic(dispatch_data)))
    )

    ambient_alert = (dispatch_data.get("ambient_alert") or "").strip()
    if ambient_alert:
        blocks.append(DividerBlock())
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(text=f":warning: *Ambient Alert*\n{ambient_alert}")
            )
        )

    button_value = json.dumps(
        {
            "shelter_id": shelter.get("id"),
            "shelter_name": shelter.get("name"),
            "shelter_address": shelter.get("address"),
            "shelter_capacity_remaining": shelter.get("capacity_remaining"),
            "shelter_image": shelter.get("image"),
            "shelter_phone": shelter.get("phone"),
            "shelter_meals": shelter.get("meals_provided"),
            "volunteer_id": volunteer.get("volunteer_id"),
            "volunteer_name": volunteer.get("name"),
            "volunteer_vehicle_type": volunteer.get("vehicle_type"),
            "volunteer_distance_miles": volunteer.get("distance_miles"),
            "volunteer_eta_minutes": volunteer.get("eta_minutes"),
            "volunteer_image": volunteer.get("image"),
            "volunteer_languages": volunteer.get("languages"),
            "client_need": client_need,
            "ambient_alert": ambient_alert,
            "rejected_candidates": dispatch_data.get("rejected_candidates") or [],
            "beds_to_lock": dispatch_data.get("beds_to_lock", 1),
        }
    )

    blocks.append(DividerBlock())
    blocks.append(
        SectionBlock(
            block_id="dispatch_actions",
            text=MarkdownTextObject(
                text="Confirm to lock the bed(s) and dispatch the volunteer."
            ),
            accessory=ButtonElement(
                action_id=DISPATCH_BUTTON_ACTION_ID,
                text=PlainTextObject(text=":lock: Confirm & Dispatch", emoji=True),
                style="primary",
                value=button_value,
            ),
        )
    )
    return blocks


def build_processing_card(dispatch_data: dict) -> list[Block]:
    """State 2: Processing — button clicked, agent is executing tools."""
    shelter = dispatch_data.get("shelter", {}) or {}
    volunteer = dispatch_data.get("volunteer", {}) or {}
    client_need = dispatch_data.get("client_need", "")

    blocks: list[Block] = [
        HeaderBlock(
            text=PlainTextObject(
                text=":arrows_counterclockwise: Locking resources and dispatching...",
                emoji=True,
            )
        ),
        DividerBlock(),
    ]

    if client_need:
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(
                    text=f":bust_in_silhouette: *Client Need*\n{client_need}"
                )
            )
        )
        blocks.append(DividerBlock())

    blocks.append(_build_shelter_section(shelter))
    blocks.append(_build_volunteer_section(volunteer))
    blocks.append(
        ContextBlock(
            elements=[
                MarkdownTextObject(text="Processing dispatch — please wait.")
            ]
        )
    )
    return blocks


def build_confirmed_card(dispatch_data: dict, timestamp_str: str) -> list[Block]:
    """State 3: Confirmed — dispatch complete, resources locked."""
    shelter = dispatch_data.get("shelter", {}) or {}
    volunteer = dispatch_data.get("volunteer", {}) or {}
    client_need = dispatch_data.get("client_need", "")

    blocks: list[Block] = [
        HeaderBlock(
            text=PlainTextObject(
                text=f":white_check_mark: DISPATCH CONFIRMED at {timestamp_str}",
                emoji=True,
            )
        ),
        DividerBlock(),
    ]

    if client_need:
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(
                    text=f":bust_in_silhouette: *Client Need*\n{client_need}"
                )
            )
        )
        blocks.append(DividerBlock())

    blocks.append(_build_shelter_section(shelter))
    # Confirmed state: show "Dispatched" instead of ETA
    blocks.append(_build_volunteer_section(volunteer, show_eta=False))
    blocks.append(
        ContextBlock(
            elements=[
                MarkdownTextObject(text="Audit log posted to #lifeline-logs.")
            ]
        )
    )
    return blocks


def build_partial_match_card(dispatch_data: dict, findings_text: str = "") -> list[Block]:
    """Partial match: shelter found, but no compatible volunteer available.

    findings_text is intentionally unused — the card is 100% structured from JSON.
    """
    shelter = dispatch_data.get("shelter", {}) or {}
    client_need = dispatch_data.get("client_need", "")

    blocks: list[Block] = [
        HeaderBlock(
            text=PlainTextObject(
                text=":rotating_light: PARTIAL MATCH — SHELTER FOUND, TRANSPORT NEEDED",
                emoji=True,
            )
        ),
        DividerBlock(),
    ]

    if client_need:
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(
                    text=f":bust_in_silhouette: *Client Need*\n{client_need}"
                )
            )
        )
        blocks.append(DividerBlock())

    blocks.append(_build_shelter_section(shelter))
    blocks.append(
        SectionBlock(
            text=MarkdownTextObject(text=":car: *Transport*\nNo compatible volunteer found.")
        )
    )
    blocks.append(
        ContextBlock(
            elements=[
                MarkdownTextObject(
                    text=f"Shelter ID: `{shelter.get('id', 'N/A')}`"
                )
            ]
        )
    )
    blocks.append(DividerBlock())

    blocks.append(
        SectionBlock(text=MarkdownTextObject(text=_build_routing_logic(dispatch_data)))
    )

    ambient_alert = (dispatch_data.get("ambient_alert") or "").strip()
    if ambient_alert:
        blocks.append(DividerBlock())
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(text=f":warning: *Ambient Alert*\n{ambient_alert}")
            )
        )

    recommendation = (dispatch_data.get("recommendation") or "").strip()
    if recommendation:
        blocks.append(DividerBlock())
        blocks.append(
            SectionBlock(
                text=MarkdownTextObject(
                    text=f":bulb: *Recommendation*\n{recommendation}"
                )
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

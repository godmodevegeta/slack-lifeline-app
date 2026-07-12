import json
import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from pyairtable import Api
import gspread

# ==========================================
# LOGGING SETUP
# ==========================================
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("mcp_server.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("lifeline.mcp")

# Quieten noisy third-party loggers so our logs stay readable
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("gspread").setLevel(logging.WARNING)
logging.getLogger("pyairtable").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

load_dotenv()
logger.info("Environment variables loaded from .env")

# Initialize a single unified MCP Server
mcp = FastMCP("Lifeline-Unified-MCP")
logger.info("FastMCP server instance created: Lifeline-Unified-MCP")

# ==========================================
# 1. ONTOLOGY CONFIGURATION
# ==========================================
ontology_path = os.path.join(os.path.dirname(__file__), "ontology.json")
logger.debug("Loading ontology from: %s", ontology_path)
try:
    with open(ontology_path, 'r') as f:
        ONTOLOGY = json.load(f)
    logger.info(
        "Ontology loaded — top-level keys: %s",
        list(ONTOLOGY.keys()),
    )
except FileNotFoundError:
    logger.critical("ontology.json NOT FOUND at %s — server cannot start", ontology_path)
    raise
except json.JSONDecodeError as exc:
    logger.critical("ontology.json is invalid JSON: %s", exc)
    raise

# ==========================================
# 2. AIRTABLE CONFIGURATION (Shelters)
# ==========================================
logger.debug("Initialising Airtable client")
try:
    api = Api(os.getenv("AIRTABLE_PAT"))
    airtable_table = api.table("apptIDO8OdVgMsyw5", "tbl8zws93R3M0ulsY")
    logger.info("Airtable client ready (base=apptIDO8OdVgMsyw5, table=tbl8zws93R3M0ulsY)")
except Exception as exc:
    logger.critical("Failed to initialise Airtable client: %s", exc, exc_info=True)
    raise

# ==========================================
# 3. GOOGLE SHEETS CONFIGURATION (Transport)
# ==========================================
credentials_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
logger.debug("Initialising Google Sheets client with credentials: %s", credentials_path)
try:
    gc = gspread.service_account(filename=credentials_path)
    logger.info("Google Sheets service account authenticated")
    spreadsheet = gc.open("slack_lifeline")
    logger.info("Spreadsheet 'slack_lifeline' opened successfully")
except FileNotFoundError:
    logger.critical("credentials.json NOT FOUND at %s", credentials_path)
    raise
except Exception as exc:
    logger.critical("Failed to open Google Sheets spreadsheet: %s", exc, exc_info=True)
    raise

try:
    volunteer_sheet = spreadsheet.worksheet("volunteer_roster")
    logger.info("Worksheet 'volunteer_roster' loaded")
except gspread.exceptions.WorksheetNotFound:
    logger.warning(
        "Worksheet 'volunteer_roster' not found — falling back to sheet1"
    )
    volunteer_sheet = spreadsheet.sheet1
    logger.info("Using fallback sheet: sheet1 (gid=%s)", volunteer_sheet.id)


# ==========================================
# ONTOLOGY LOGIC
# ==========================================

def get_mobility_aid(aid_name):
    logger.debug("get_mobility_aid called with: %r", aid_name)
    result = ONTOLOGY.get('object_classes', {}).get('mobility_aid', {}).get('instances', {}).get(aid_name.lower())
    if result is None:
        logger.debug("No mobility_aid entry found for %r", aid_name)
    else:
        logger.debug("Resolved mobility_aid %r -> %s", aid_name, result)
    return result

def get_vehicle(vehicle_name):
    logger.debug("get_vehicle called with: %r", vehicle_name)
    result = ONTOLOGY.get('object_classes', {}).get('vehicle', {}).get('instances', {}).get(vehicle_name.lower())
    if result is None:
        logger.warning("Unknown vehicle type in ontology: %r", vehicle_name)
    else:
        logger.debug("Resolved vehicle %r -> %s", vehicle_name, result)
    return result

def _check_compatibility(client_needs: list[str], vehicle_type: str, family_size: int | None = None) -> dict:
    """Deterministic physics validator (internal helper).

    Checks if a specific vehicle type physically supports a list of client
    mobility/companion needs. Returns {"compatible": bool, "reason": str}.
    Raises nothing — all unknown keys are handled gracefully.
    """
    vehicle = get_vehicle(vehicle_type)
    if not vehicle:
        return {"compatible": False, "reason": f"Unknown vehicle type: {vehicle_type}"}

    missing_features = []

    for need in client_needs or []:
        aid = get_mobility_aid(need)
        if not aid:
            # Check if it's an animal
            animal = ONTOLOGY.get('object_classes', {}).get('animal', {}).get('instances', {}).get(need.lower())
            if animal:
                req = animal.get("vehicle_requirement", "")
                if req == "carrier_required":
                    req_space = animal.get("space_required_cu_ft", 0)
                    if vehicle.get("cargo_space_cu_ft", 0) < req_space:
                        missing_features.append(f"{need} needs {req_space} cu ft cargo space (vehicle has {vehicle.get('cargo_space_cu_ft', 0)})")
                elif req == "rides_with_handler_or_cargo":
                    req_space = animal.get("space_required_cu_ft", 0)
                    if vehicle.get("cargo_space_cu_ft", 0) < req_space and vehicle.get("passenger_capacity", 0) < (family_size or 0) + 1:
                        missing_features.append(f"{need} needs {req_space} cu ft cargo or extra passenger seat")
            else:
                logger.debug("Need %r not found in mobility_aid or animal ontology — skipping", need)
            continue

        # 1. Lift check — use lift_capacity_min_lbs if defined, else weight_lbs
        if aid.get("requires_lift"):
            if not vehicle.get("lift_equipped"):
                missing_features.append(f"{need} requires a vehicle with a lift")
            else:
                lift_required = aid.get("lift_capacity_min_lbs") or aid.get("weight_lbs", 0)
                if vehicle.get("lift_capacity_lbs", 0) < lift_required:
                    missing_features.append(
                        f"{need} requires lift capacity >= {lift_required} lbs (vehicle has {vehicle.get('lift_capacity_lbs')} lbs)"
                    )

        # 2. Ramp check
        if aid.get("requires_ramp"):
            if not vehicle.get("ramp_equipped"):
                missing_features.append(f"{need} requires a vehicle with a ramp")

        # 3. Cargo space check
        req_cargo = aid.get("cargo_space_cu_ft") or 0
        if req_cargo > 0:
            veh_cargo = vehicle.get("cargo_space_cu_ft") or 0
            if veh_cargo < req_cargo:
                missing_features.append(f"{need} requires {req_cargo} cu ft cargo space (vehicle has {veh_cargo})")

        # 4. Power requirement check
        power_req = aid.get("power_requirement")
        if power_req:
            veh_outlets = vehicle.get("power_outlets", [])
            if power_req not in veh_outlets:
                missing_features.append(f"{need} requires {power_req} power outlet (vehicle has {veh_outlets})")

        # 5. Securement points check
        req_secure = aid.get("securement_points") or 0
        if req_secure > 0:
            veh_secure = vehicle.get("securement_points") or 0
            if veh_secure < req_secure:
                missing_features.append(f"{need} requires {req_secure} securement points (vehicle has {veh_secure})")

        # 6. Wheelchair slots check (if aid requires lift or ramp, it occupies a wheelchair slot)
        if aid.get("requires_lift") or aid.get("requires_ramp"):
            veh_slots = vehicle.get("wheelchair_slots", 0)
            if veh_slots < 1:
                missing_features.append(f"{need} requires a wheelchair slot (vehicle has {veh_slots})")

    # 7. Passenger capacity check
    if family_size and family_size > 0:
        veh_capacity = vehicle.get("passenger_capacity", 0)
        if veh_capacity < family_size:
            missing_features.append(f"Family of {family_size} exceeds vehicle passenger capacity ({veh_capacity})")

    if missing_features:
        return {
            "compatible": False,
            "reason": f"Vehicle '{vehicle_type}' lacks physical capabilities: {missing_features}. DO NOT DISPATCH THIS VEHICLE."
        }
    return {
        "compatible": True,
        "reason": f"Vehicle '{vehicle_type}' has all required physical features for {client_needs}."
    }


@mcp.tool()
def evaluate_physical_compatibility(client_needs: list[str], vehicle_type: str, family_size: int | None = None) -> dict:
    """
    DETERMINISTIC PHYSICS VALIDATOR.
    Checks if a specific vehicle type physically supports a list of client mobility/companion needs.
    Can be called directly to re-validate a specific vehicle, but `search_volunteers` now does
    this internally when you pass `client_needs`.

    Args:
        client_needs: List of ontology keys required by the client (e.g., ['motorized_wheelchair', 'service_dog']).
        vehicle_type: The ontology key of the vehicle (e.g., 'passenger_van', 'mobility_van').
        family_size: Optional. Total people in the client's family (for passenger capacity check).
    """
    logger.info(
        "evaluate_physical_compatibility | vehicle_type=%r | client_needs=%s | family_size=%s",
        vehicle_type, client_needs, family_size,
    )
    result = _check_compatibility(client_needs, vehicle_type, family_size=family_size)
    if result["compatible"]:
        logger.info(
            "evaluate_physical_compatibility | COMPATIBLE | vehicle=%r | needs=%s",
            vehicle_type, client_needs,
        )
    else:
        logger.warning(
            "evaluate_physical_compatibility | INCOMPATIBLE | vehicle=%r | reason=%s",
            vehicle_type, result["reason"],
        )
    return result

# ==========================================
# SHELTER LOGIC (Airtable)
# ==========================================

_STREET_ABBREVIATIONS = {
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "rd": "road",
    "ln": "lane",
    "dr": "drive",
    "ct": "court",
    "pl": "place",
}


def _normalize_street(s: str) -> str:
    """Normalize a street name for robust matching.

    Lowercases, strips periods, and expands common abbreviations so that
    'Mission St' matches '1420 Mission Street' and vice-versa.
    """
    if not s:
        return ""
    s = s.lower().strip()
    # Tokenize on whitespace, normalize each token
    tokens = []
    for tok in s.replace(".", " ").split():
        tokens.append(_STREET_ABBREVIATIONS.get(tok, tok))
    return " ".join(tokens)


def _street_matches(shelter_address, exclude_streets: list[str]) -> bool:
    """Return True if any exclude_streets entry matches the shelter's address.

    Handles Airtable address-type fields (lists) and plain strings.
    """
    if not exclude_streets:
        return False
    # Airtable address fields come back as lists; element [0] is the street line.
    if isinstance(shelter_address, list):
        street_line = shelter_address[0] if shelter_address else ""
    else:
        street_line = shelter_address or ""
    norm_shelter = _normalize_street(street_line)
    if not norm_shelter:
        return False
    for street in exclude_streets:
        if _normalize_street(street) in norm_shelter:
            return True
    return False


@mcp.tool()
def search_shelters(
    zone: str,
    min_capacity: int,
    allow_pets: bool = False,
    wheelchair_accessible: bool = False,
    exclude_streets: list[str] | None = None,
) -> list[dict]:
    """
    Searches the Shelter Roster for available beds based on zone and capacity.
    Results are sorted deterministically: capacity_remaining DESC, then shelter_id ASC.

    The 'wheelchair_accessible' boolean is user-reported and often inaccurate. Pass
    `exclude_streets` (street names extracted from #logistics-alerts hazards) to
    filter out shelters on closed streets before returning.

    Args:
        zone: The target zone (e.g., 'A', 'B', 'C').
        min_capacity: Minimum number of beds required.
        allow_pets: If True, only return shelters that allow pets.
        wheelchair_accessible: If True, filter for wheelchair accessible (but verify with Ontology).
        exclude_streets: Optional list of street names to exclude (e.g., ['Mission St']).
    """
    logger.info(
        "search_shelters | zone=%r | min_capacity=%d | allow_pets=%s | wheelchair_accessible=%s | exclude_streets=%s",
        zone, min_capacity, allow_pets, wheelchair_accessible, exclude_streets,
    )

    formula = f"AND({{Zone}} = '{zone}', {{Capacity Remaining}} >= {min_capacity}"
    if allow_pets:
        formula += ", {Pet Friendly} = TRUE()"
    if wheelchair_accessible:
        formula += ", {Wheelchair Accessible} = TRUE()"
    formula += ")"

    logger.debug("search_shelters | Airtable formula: %s", formula)

    try:
        # Sort at DB level: '-' prefix = descending (pyairtable string-list syntax).
        records = airtable_table.all(formula=formula, sort=['-Capacity Remaining'])
        logger.info("search_shelters | Airtable returned %d record(s)", len(records))
    except Exception as exc:
        logger.error("search_shelters | Airtable query failed: %s", exc, exc_info=True)
        raise

    # Format for LLM readability
    results = [
        {
            "id": rec["id"],
            "name": rec["fields"].get("Shelter Name"),
            "address": rec["fields"].get("Address"),
            "capacity_remaining": rec["fields"].get("Capacity Remaining"),
            "phone": rec["fields"].get("Phone"),
            "meals_provided": rec["fields"].get("Meals Provided"),
            "image": rec["fields"].get("Asset"),
            "notes": rec["fields"].get("Notes")
        }
        for rec in records
    ]

    # Filter out shelters on closed streets (robust normalized matching)
    if exclude_streets:
        before = len(results)
        results = [r for r in results if not _street_matches(r.get("address"), exclude_streets)]
        logger.info(
            "search_shelters | excluded %d shelter(s) on closed streets: %s",
            before - len(results), exclude_streets,
        )

    # Secondary sort: capacity DESC (already from DB), then shelter_id ASC for ties.
    results.sort(key=lambda r: (-(r.get("capacity_remaining") or 0), r.get("id") or ""))

    logger.debug("search_shelters | formatted results: %s", results)
    return results

@mcp.tool()
def lock_shelter_capacity(shelter_id: str, beds_to_lock: int, initiated_by_user: str) -> dict:
    """
    DECREMENTS the 'Capacity Remaining' in Airtable to prevent double-booking.
    MUST be called before dispatching a volunteer.
    
    Args:
        shelter_id: The Airtable Record ID of the shelter.
        beds_to_lock: Number of beds to reserve (usually matches client family size).
        initiated_by_user: The Slack User ID of the dispatcher who clicked 'Approve'.
    """
    logger.info(
        "lock_shelter_capacity | shelter_id=%r | beds_to_lock=%d | initiated_by=%r",
        shelter_id, beds_to_lock, initiated_by_user,
    )

    try:
        record = airtable_table.get(shelter_id)
        logger.debug("lock_shelter_capacity | fetched record: %s", record)
    except Exception as exc:
        logger.error("lock_shelter_capacity | Failed to fetch shelter record %r: %s", shelter_id, exc, exc_info=True)
        raise

    current_cap = record["fields"].get("Capacity Remaining", 0)
    logger.debug("lock_shelter_capacity | current capacity: %d", current_cap)
    
    if current_cap < beds_to_lock:
        result = {"status": "error", "message": f"Failed: Only {current_cap} beds left."}
        logger.warning(
            "lock_shelter_capacity | CAPACITY ERROR | shelter=%r | requested=%d | available=%d",
            shelter_id, beds_to_lock, current_cap,
        )
        return result
        
    new_cap = current_cap - beds_to_lock
    logger.debug("lock_shelter_capacity | updating capacity %d -> %d", current_cap, new_cap)

    try:
        airtable_table.update(shelter_id, {"Capacity Remaining": new_cap})
        logger.info(
            "lock_shelter_capacity | SUCCESS | shelter=%r | locked=%d | new_capacity=%d | by=%r",
            shelter_id, beds_to_lock, new_cap, initiated_by_user,
        )
    except Exception as exc:
        logger.error(
            "lock_shelter_capacity | Airtable update failed for shelter %r: %s",
            shelter_id, exc, exc_info=True,
        )
        raise
    
    return {
        "status": "success",
        "message": f"Locked {beds_to_lock} beds at {record['fields']['Shelter Name']}. New capacity: {new_cap}.",
        "new_capacity": new_cap,
        "audit_log": f"User {initiated_by_user} locked {beds_to_lock} beds."
    }

# ==========================================
# TRANSPORT LOGIC (Google Sheets)
# ==========================================

@mcp.tool()
def search_volunteers(
    zone: str,
    vehicle_type: str = None,
    available_now: bool = True,
    client_needs: list[str] | None = None,
    family_size: int | None = None,
) -> dict:
    """
    Searches the Volunteer Transport Roster for available drivers.

    Pass `client_needs` (ontology keys like ['motorized_wheelchair', 'service_dog'])
    AND `family_size` to have the tool internally validate each volunteer's vehicle
    against the physical ontology and return only compatible volunteers. The response
    includes a `rejected_candidates` array showing which volunteers were excluded and why.

    Results are sorted deterministically: distance_miles ASC, then volunteer_id ASC.

    Returns each volunteer's 'volunteer_id' — always pass this (not the name) to
    dispatch_volunteer to guarantee the correct row is updated.

    Args:
        zone: Target zone (e.g., 'A').
        vehicle_type: Specific vehicle class if known (e.g., 'passenger_van', 'suv').
        available_now: Filter by 'Available Now' column.
        client_needs: Optional list of ontology keys to pre-filter by physical compatibility.
        family_size: Optional. Total people in the client's family (for passenger capacity check).
    """
    logger.info(
        "search_volunteers | zone=%r | vehicle_type=%r | available_now=%s | client_needs=%s | family_size=%s",
        zone, vehicle_type, available_now, client_needs, family_size,
    )

    try:
        records = volunteer_sheet.get_all_records()
        logger.debug("search_volunteers | total rows in sheet: %d", len(records))
    except Exception as exc:
        logger.error("search_volunteers | Failed to read volunteer sheet: %s", exc, exc_info=True)
        raise

    matches = []

    for row in records:
        if row["Zone"] == zone and str(row["Available Now"]).upper() == str(available_now).upper():
            if vehicle_type and row["Vehicle Type"].lower() != vehicle_type.lower():
                logger.debug(
                    "search_volunteers | skipping volunteer %r — vehicle mismatch (%r != %r)",
                    row.get("Volunteer_ID"), row["Vehicle Type"], vehicle_type,
                )
                continue
            matches.append({
                "volunteer_id": row["Volunteer_ID"],
                "name": row["Volunteer Name"],
                "phone": row["Phone"],
                "vehicle_type": row["Vehicle Type"],
                "languages": row["Languages"],
                "distance_miles": row["Distance Miles"],
                "eta_minutes": row.get("ETA Minutes"),
                "training_level": row.get("Training Level"),
                "image": row.get("asset"),
                "notes": row["Notes"]
            })

    logger.info(
        "search_volunteers | %d pre-filter match(es) for zone=%r, vehicle_type=%r",
        len(matches), zone, vehicle_type,
    )

    # If client_needs provided, run deterministic ontology validation internally.
    compatible_volunteers = []
    rejected_candidates = []
    warnings = []

    if client_needs:
        for vol in matches:
            vtype = vol.get("vehicle_type", "")
            try:
                result = _check_compatibility(client_needs, vtype, family_size=family_size)
                if result.get("compatible"):
                    compatible_volunteers.append(vol)
                else:
                    rejected_candidates.append({
                        "volunteer_id": vol.get("volunteer_id"),
                        "name": vol.get("name"),
                        "vehicle_type": vtype,
                        "reason": result.get("reason", "unknown"),
                    })
            except Exception as exc:
                # Bulletproof: never crash on LLM hallucinations or bad data.
                # Fall back to including the volunteer unfiltered.
                logger.error(
                    "search_volunteers | compatibility check failed for %r (%r): %s — including unfiltered",
                    vol.get("volunteer_id"), vtype, exc, exc_info=True,
                )
                compatible_volunteers.append(vol)
                warnings.append(
                    f"Compatibility check failed for {vol.get('name')} ({vtype}): {exc}"
                )
    else:
        compatible_volunteers = matches

    # Deterministic sort: distance_miles ASC, then volunteer_id ASC.
    compatible_volunteers.sort(
        key=lambda v: (v.get("distance_miles") or 0, v.get("volunteer_id") or "")
    )

    response = {
        "compatible_volunteers": compatible_volunteers,
        "rejected_candidates": rejected_candidates,
    }
    if warnings:
        response["warning"] = "; ".join(warnings)

    logger.info(
        "search_volunteers | compatible=%d rejected=%d | zone=%r",
        len(compatible_volunteers), len(rejected_candidates), zone,
    )
    logger.debug("search_volunteers | response: %s", response)
    return response

@mcp.tool()
def dispatch_volunteer(volunteer_id: str, shelter_name: str, initiated_by_user: str) -> dict:
    """
    Updates the volunteer's status in Sheets to 'DISPATCHED' and logs the audit.
    
    Uses volunteer_id (from column A) for lookup — not the volunteer's name — to
    guarantee the correct row is updated even if two volunteers share the same name.
    
    Args:
        volunteer_id: The unique Volunteer_ID from search_volunteers results (e.g. 'V001').
        shelter_name: The shelter they are being dispatched to.
        initiated_by_user: Slack User ID of the dispatcher.
    """
    logger.info(
        "dispatch_volunteer | volunteer_id=%r | shelter=%r | initiated_by=%r",
        volunteer_id, shelter_name, initiated_by_user,
    )

    try:
        cell = volunteer_sheet.find(volunteer_id, in_column=1)  # Column A = Volunteer_ID
        logger.debug("dispatch_volunteer | cell lookup result: %s", cell)
    except Exception as exc:
        logger.error(
            "dispatch_volunteer | Sheet lookup failed for volunteer_id=%r: %s",
            volunteer_id, exc, exc_info=True,
        )
        raise

    if not cell:
        result = {"status": "error", "message": f"Volunteer ID '{volunteer_id}' not found."}
        logger.warning("dispatch_volunteer | volunteer_id=%r not found in sheet column A", volunteer_id)
        return result
    
    logger.debug("dispatch_volunteer | found at row %d", cell.row)

    # Read back the name from the same row for a human-readable confirmation message
    try:
        volunteer_name = volunteer_sheet.cell(cell.row, 2).value  # Column B = Volunteer Name
        logger.debug("dispatch_volunteer | volunteer name resolved: %r", volunteer_name)
    except Exception as exc:
        logger.error(
            "dispatch_volunteer | Failed to read volunteer name at row %d: %s",
            cell.row, exc, exc_info=True,
        )
        raise
    
    # Find the 'Available Now' column index dynamically from the header row
    try:
        headers = volunteer_sheet.row_values(1)
        logger.debug("dispatch_volunteer | sheet headers: %s", headers)
    except Exception as exc:
        logger.error("dispatch_volunteer | Failed to read sheet headers: %s", exc, exc_info=True)
        raise

    try:
        available_col = headers.index("Available Now") + 1
        logger.debug("dispatch_volunteer | 'Available Now' column index: %d", available_col)
    except ValueError:
        result = {"status": "error", "message": "'Available Now' column not found in sheet headers."}
        logger.error(
            "dispatch_volunteer | 'Available Now' column missing from headers: %s", headers
        )
        return result
    
    try:
        volunteer_sheet.update_cell(cell.row, available_col, "FALSE")
        logger.info(
            "dispatch_volunteer | SUCCESS | volunteer=%r (id=%r) marked DISPATCHED to %r | by=%r",
            volunteer_name, volunteer_id, shelter_name, initiated_by_user,
        )
    except Exception as exc:
        logger.error(
            "dispatch_volunteer | Failed to update cell (row=%d, col=%d) for volunteer %r: %s",
            cell.row, available_col, volunteer_id, exc, exc_info=True,
        )
        raise

    return {
        "status": "success",
        "message": f"{volunteer_name} (ID: {volunteer_id}) marked as DISPATCHED to {shelter_name}.",
        "audit_log": f"User {initiated_by_user} dispatched {volunteer_name} ({volunteer_id}) to {shelter_name}."
    }

# ==========================================
# SERVER EXECUTION
# ==========================================

if __name__ == "__main__":
    logger.info("Starting Lifeline MCP server on port 8000 (transport=http)")
    mcp.run(transport="http", port=8000)
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

@mcp.tool()
def evaluate_physical_compatibility(client_needs: list[str], vehicle_type: str) -> dict:
    """
    DETERMINISTIC PHYSICS VALIDATOR. 
    Checks if a specific vehicle type physically supports a list of client mobility/companion needs.
    MUST be called before finalizing any transport dispatch.
    
    Args:
        client_needs: List of ontology keys required by the client (e.g., ['motorized_wheelchair', 'service_animal']).
        vehicle_type: The ontology key of the vehicle (e.g., 'passenger_van', 'mobility_van').
    """
    logger.info(
        "evaluate_physical_compatibility | vehicle_type=%r | client_needs=%s",
        vehicle_type, client_needs,
    )

    vehicle = get_vehicle(vehicle_type)
    if not vehicle:
        result = {"compatible": False, "reason": f"Unknown vehicle type: {vehicle_type}"}
        logger.warning("evaluate_physical_compatibility | INCOMPATIBLE | %s", result["reason"])
        return result
        
    missing_features = []
    
    for need in client_needs:
        logger.debug("evaluate_physical_compatibility | checking need: %r", need)
        aid = get_mobility_aid(need)
        if not aid:
            # Check if it's an animal in the other class
            animal = ONTOLOGY.get('object_classes', {}).get('animal', {}).get('instances', {}).get(need.lower())
            if animal:
                logger.debug("Need %r resolved as animal: %s", need, animal)
                if animal.get("vehicle_requirement") == "carrier_required":
                    req_space = animal.get("space_required_cu_ft", 0)
                    if vehicle.get("cargo_space_cu_ft", 0) < req_space:
                        msg = f"{need} needs {req_space} cu ft of cargo space"
                        logger.debug("Cargo space failure for animal %r: %s", need, msg)
                        missing_features.append(msg)
            else:
                logger.debug("Need %r not found in mobility_aid or animal ontology — skipping", need)
            continue
            
        # 1. Lift check
        if aid.get("requires_lift"):
            if not vehicle.get("lift_equipped"):
                msg = f"{need} requires a vehicle with a lift"
                logger.debug("Lift check failed: %s", msg)
                missing_features.append(msg)
            elif vehicle.get("lift_capacity_lbs", 0) < aid.get("weight_lbs", 0):
                msg = f"{need} ({aid.get('weight_lbs')} lbs) exceeds vehicle lift capacity ({vehicle.get('lift_capacity_lbs')} lbs)"
                logger.debug("Lift capacity exceeded: %s", msg)
                missing_features.append(msg)
                
        # 2. Ramp check
        if aid.get("requires_ramp"):
            if not vehicle.get("ramp_equipped"):
                msg = f"{need} requires a vehicle with a ramp"
                logger.debug("Ramp check failed: %s", msg)
                missing_features.append(msg)
            else:
                ramp_width = vehicle.get("ramp_width_in") or 0
                aid_width = aid.get("unfolded_dimensions_in", {}).get("w", 0)
                if ramp_width < aid_width:
                    msg = f"{need} requires ramp width >= {aid_width} in (vehicle has {ramp_width} in)"
                    logger.debug("Ramp width insufficient: %s", msg)
                    missing_features.append(msg)
                    
        # 3. Cargo space check
        req_cargo = aid.get("cargo_space_cu_ft") or 0
        if req_cargo > 0:
            veh_cargo = vehicle.get("cargo_space_cu_ft") or 0
            if veh_cargo < req_cargo:
                msg = f"{need} requires {req_cargo} cu ft cargo space (vehicle has {veh_cargo})"
                logger.debug("Cargo space check failed: %s", msg)
                missing_features.append(msg)
                
        # 4. Power requirement check
        power_req = aid.get("power_requirement")
        if power_req:
            veh_outlets = vehicle.get("power_outlets", [])
            if power_req not in veh_outlets:
                msg = f"{need} requires {power_req} power outlet"
                logger.debug("Power outlet check failed: %s", msg)
                missing_features.append(msg)
                
        # 5. Securement points check
        req_secure = aid.get("securement_points") or 0
        if req_secure > 0:
            veh_secure = vehicle.get("securement_points") or 0
            if veh_secure < req_secure:
                msg = f"{need} requires {req_secure} securement points (vehicle has {veh_secure})"
                logger.debug("Securement points check failed: %s", msg)
                missing_features.append(msg)

    if missing_features:
        result = {
            "compatible": False,
            "reason": f"Vehicle '{vehicle_type}' lacks physical capabilities: {missing_features}. DO NOT DISPATCH THIS VEHICLE."
        }
        logger.warning(
            "evaluate_physical_compatibility | INCOMPATIBLE | vehicle=%r | failures=%s",
            vehicle_type, missing_features,
        )
        return result
        
    logger.info(
        "evaluate_physical_compatibility | COMPATIBLE | vehicle=%r | needs=%s",
        vehicle_type, client_needs,
    )
    return {
        "compatible": True,
        "reason": f"Vehicle '{vehicle_type}' has all required physical features for {client_needs}."
    }

# ==========================================
# SHELTER LOGIC (Airtable)
# ==========================================

@mcp.tool()
def search_shelters(zone: str, min_capacity: int, allow_pets: bool = False, wheelchair_accessible: bool = False) -> list[dict]:
    """
    Searches the Shelter Roster for available beds based on zone and capacity.
    
    CRITICAL INSTRUCTION FOR LLM: The 'wheelchair_accessible' boolean in this database 
    is user-reported and often inaccurate. Use this tool ONLY to get a broad list of 
    potential shelters. You MUST pass the specific shelter's vehicle/transport details 
    to the 'evaluate_physical_compatibility' tool to verify physical constraints.
    
    Args:
        zone: The target zone (e.g., 'A', 'B', 'C').
        min_capacity: Minimum number of beds required.
        allow_pets: If True, only return shelters that allow pets.
        wheelchair_accessible: If True, filter for wheelchair accessible (but verify with Ontology).
    """
    logger.info(
        "search_shelters | zone=%r | min_capacity=%d | allow_pets=%s | wheelchair_accessible=%s",
        zone, min_capacity, allow_pets, wheelchair_accessible,
    )

    formula = f"AND({{Zone}} = '{zone}', {{Capacity Remaining}} >= {min_capacity}"
    if allow_pets:
        formula += ", {Pet Friendly} = TRUE()"
    if wheelchair_accessible:
        formula += ", {Wheelchair Accessible} = TRUE()"
    formula += ")"

    logger.debug("search_shelters | Airtable formula: %s", formula)

    try:
        records = airtable_table.all(formula=formula)
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
            "notes": rec["fields"].get("Notes")
        }
        for rec in records
    ]

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
        "audit_log": f"User {initiated_by_user} locked {beds_to_lock} beds."
    }

# ==========================================
# TRANSPORT LOGIC (Google Sheets)
# ==========================================

@mcp.tool()
def search_volunteers(zone: str, vehicle_type: str = None, available_now: bool = True) -> list[dict]:
    """
    Searches the Volunteer Transport Roster for available drivers.
    
    CRITICAL INSTRUCTION FOR LLM: Do NOT trust the 'Wheelchair Accessible' column blindly. 
    A volunteer might mark their minivan as 'TRUE' because a folding chair fits in the trunk.
    You MUST use the 'evaluate_physical_compatibility' tool with the 'vehicle_type' to 
    verify if the vehicle physically supports the client's specific mobility device.
    
    Returns each volunteer's 'volunteer_id' — always pass this (not the name) to
    dispatch_volunteer to guarantee the correct row is updated.
    
    Args:
        zone: Target zone (e.g., 'A').
        vehicle_type: Specific vehicle class if known (e.g., 'van', 'suv').
        available_now: Filter by 'Available Now' column.
    """
    logger.info(
        "search_volunteers | zone=%r | vehicle_type=%r | available_now=%s",
        zone, vehicle_type, available_now,
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
                "volunteer_id": row["Volunteer_ID"],  # Unique ID from column A — use for dispatch
                "name": row["Volunteer Name"],
                "phone": row["Phone"],
                "vehicle_type": row["Vehicle Type"],
                "languages": row["Languages"],
                "distance_miles": row["Distance Miles"],
                "notes": row["Notes"]
            })
            
    logger.info(
        "search_volunteers | %d match(es) found for zone=%r, vehicle_type=%r",
        len(matches), zone, vehicle_type,
    )
    logger.debug("search_volunteers | matches: %s", matches)
    return matches

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
        volunteer_sheet.update_cell(cell.row, available_col, "DISPATCHED")
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
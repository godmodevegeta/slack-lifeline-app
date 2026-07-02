from dataclasses import dataclass
from typing import Optional

from slack_sdk import WebClient


@dataclass
class AgentDeps:
    client: WebClient
    user_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    user_token: str | None = None
    
    # Lifeline dispatch context
    dispatch_id: Optional[str] = None
    matched_shelter: Optional[dict] = None
    matched_volunteer: Optional[dict] = None
    ambient_alerts: Optional[str] = None
    intake_channel_id: str = "C0BC7QVUTFT"
    logs_channel_id: str = "C0BCVF0J5QT"

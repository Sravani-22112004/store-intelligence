from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import datetime
from enum import Enum
import uuid


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return v


class EventBatch(BaseModel):
    events: List[StoreEvent] = Field(..., max_length=500)


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: List[dict] = []


# ── API Response Models ──────────────────────────────────────────────────────

class ZoneDwell(BaseModel):
    zone_id: str
    avg_dwell_seconds: float
    visit_count: int


class StoreMetrics(BaseModel):
    store_id: str
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: List[ZoneDwell]
    queue_depth: int
    abandonment_rate: float
    total_transactions: int


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float


class StoreFunnel(BaseModel):
    store_id: str
    funnel: List[FunnelStage]


class ZoneHeatmapEntry(BaseModel):
    zone_id: str
    visit_frequency: int
    avg_dwell_seconds: float
    normalized_score: float  # 0-100
    data_confidence: bool     # False if < 20 sessions


class StoreHeatmap(BaseModel):
    store_id: str
    zones: List[ZoneHeatmapEntry]


class Anomaly(BaseModel):
    anomaly_id: str
    type: str
    severity: Literal["INFO", "WARN", "CRITICAL"]
    description: str
    suggested_action: str
    detected_at: datetime
    zone_id: Optional[str] = None


class StoreAnomalies(BaseModel):
    store_id: str
    anomalies: List[Anomaly]


class StoreHealth(BaseModel):
    store_id: str
    status: str
    last_event_timestamp: Optional[datetime]
    feed_status: Literal["OK", "STALE_FEED", "NO_DATA"]
    lag_minutes: Optional[float]


class HealthResponse(BaseModel):
    service: str
    status: str
    stores: List[StoreHealth]
    timestamp: datetime

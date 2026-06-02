"""
emit.py — Converts tracker state into structured store events.
"""
import uuid
import json
from datetime import datetime
from typing import Optional, List, Dict
from dataclasses import dataclass


@dataclass
class StoreEvent:
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str          # ISO-8601 UTC
    zone_id: Optional[str]
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata: dict

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": self.visitor_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "zone_id": self.zone_id,
            "dwell_ms": int(self.dwell_ms),
            "is_staff": bool(self.is_staff),
            "confidence": round(float(self.confidence), 4),
            "metadata": self.metadata,
        }


class EventEmitter:
    def __init__(self, store_id: str, camera_id: str):
        self.store_id = store_id
        self.camera_id = camera_id
        self._session_counters: Dict[str, int] = {}

    def _seq(self, visitor_id: str) -> int:
        self._session_counters[visitor_id] = self._session_counters.get(visitor_id, 0) + 1
        return self._session_counters[visitor_id]

    def _make(
        self,
        visitor_id: str,
        event_type: str,
        timestamp: datetime,
        zone_id: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        confidence: float = 1.0,
        extra_meta: Optional[dict] = None,
    ) -> StoreEvent:
        meta = {"session_seq": self._seq(visitor_id)}
        if extra_meta:
            meta.update(extra_meta)
        return StoreEvent(
            event_id=str(uuid.uuid4()),
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=visitor_id,
            event_type=event_type,
            timestamp=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=is_staff,
            confidence=confidence,
            metadata=meta,
        )

    def entry(self, visitor_id: str, ts: datetime, is_staff: bool, conf: float) -> StoreEvent:
        return self._make(visitor_id, "ENTRY", ts, is_staff=is_staff, confidence=conf)

    def exit(self, visitor_id: str, ts: datetime, is_staff: bool, conf: float) -> StoreEvent:
        return self._make(visitor_id, "EXIT", ts, is_staff=is_staff, confidence=conf)

    def reentry(self, visitor_id: str, ts: datetime, conf: float) -> StoreEvent:
        return self._make(visitor_id, "REENTRY", ts, confidence=conf)

    def zone_enter(self, visitor_id: str, ts: datetime, zone_id: str, is_staff: bool, conf: float) -> StoreEvent:
        return self._make(visitor_id, "ZONE_ENTER", ts, zone_id=zone_id, is_staff=is_staff, confidence=conf)

    def zone_exit(self, visitor_id: str, ts: datetime, zone_id: str, dwell_ms: int, is_staff: bool, conf: float) -> StoreEvent:
        return self._make(visitor_id, "ZONE_EXIT", ts, zone_id=zone_id, dwell_ms=dwell_ms, is_staff=is_staff, confidence=conf)

    def zone_dwell(self, visitor_id: str, ts: datetime, zone_id: str, dwell_ms: int, is_staff: bool, conf: float) -> StoreEvent:
        return self._make(visitor_id, "ZONE_DWELL", ts, zone_id=zone_id, dwell_ms=dwell_ms, is_staff=is_staff, confidence=conf)

    def billing_queue_join(self, visitor_id: str, ts: datetime, queue_depth: int, conf: float) -> StoreEvent:
        return self._make(
            visitor_id, "BILLING_QUEUE_JOIN", ts,
            zone_id="BILLING",
            confidence=conf,
            extra_meta={"queue_depth": queue_depth},
        )

    def billing_queue_abandon(self, visitor_id: str, ts: datetime, conf: float) -> StoreEvent:
        return self._make(visitor_id, "BILLING_QUEUE_ABANDON", ts, zone_id="BILLING", confidence=conf)


def write_events_jsonl(events: List[StoreEvent], path: str):
    with open(path, "a") as f:
        for e in events:
            f.write(json.dumps(e.to_dict()) + "\n")

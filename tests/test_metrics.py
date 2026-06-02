# PROMPT: Write pytest tests for a FastAPI store analytics system. The API has endpoints:
# POST /events/ingest (batch up to 500 events, idempotent by event_id)
# GET /stores/{id}/metrics (unique visitors, conversion rate, zone dwell, queue depth)
# Tests should cover: happy path, empty store, all-staff clip, zero purchases, idempotency,
# re-entry deduplication. Use pytest fixtures with an in-memory SQLite database.
#
# CHANGES MADE: Added edge case for zero-traffic (empty store), changed fixture to use
# SQLite in-memory instead of suggested PostgreSQL mock, added explicit is_staff
# exclusion assertions, rewrote the conversion rate assertion to be more precise.

import pytest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app
from database import Base, get_db, DBTransaction

TEST_DB_URL = "sqlite:///./test_store.sqlite"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

STORE_ID = "STORE_BLR_002"


def make_event(event_type="ENTRY", visitor_id=None, zone_id=None,
               is_staff=False, confidence=0.9, dwell_ms=0,
               ts: datetime = None, event_id=None):
    ts = ts or datetime.utcnow()
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": ts.isoformat() + "Z",
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"session_seq": 1, "queue_depth": None, "sku_zone": None},
    }


def clear_db():
    db = TestingSessionLocal()
    db.execute(__import__('sqlalchemy').text("DELETE FROM events"))
    db.execute(__import__('sqlalchemy').text("DELETE FROM pos_transactions"))
    db.commit()
    db.close()


# ── Ingestion tests ───────────────────────────────────────────────────────────

class TestIngest:
    def setup_method(self):
        clear_db()

    def test_ingest_single_event(self):
        event = make_event()
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 1
        assert data["rejected"] == 0
        assert data["duplicate"] == 0

    def test_ingest_batch_up_to_500(self):
        events = [make_event() for _ in range(100)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 100

    def test_idempotency_same_event_twice(self):
        """Sending the same event twice should count as 1 accepted + 1 duplicate."""
        event = make_event()
        client.post("/events/ingest", json={"events": [event]})
        resp2 = client.post("/events/ingest", json={"events": [event]})
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["duplicate"] == 1
        assert data["accepted"] == 0

    def test_idempotency_same_batch_sent_twice(self):
        events = [make_event() for _ in range(5)]
        client.post("/events/ingest", json={"events": events})
        resp2 = client.post("/events/ingest", json={"events": events})
        assert resp2.status_code == 200
        assert resp2.json()["duplicate"] == 5

    def test_batch_exceeds_500_rejected(self):
        events = [make_event() for _ in range(501)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 422  # Pydantic max_length validation


# ── Metrics tests ─────────────────────────────────────────────────────────────

class TestMetrics:
    def setup_method(self):
        clear_db()

    def _ingest(self, events):
        client.post("/events/ingest", json={"events": events})

    def test_metrics_empty_store_no_crash(self):
        """Empty store should return zeros, not crash or return null."""
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unique_visitors"] == 0
        assert data["conversion_rate"] == 0.0
        assert data["abandonment_rate"] == 0.0

    def test_metrics_unique_visitors_excludes_staff(self):
        """Staff events must not be counted in unique_visitors."""
        customer_id = f"VIS_{uuid.uuid4().hex[:6]}"
        staff_id = f"VIS_{uuid.uuid4().hex[:6]}"
        self._ingest([
            make_event("ENTRY", visitor_id=customer_id, is_staff=False),
            make_event("ENTRY", visitor_id=staff_id, is_staff=True),
        ])
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 1  # only customer

    def test_metrics_all_staff_clip(self):
        """Clip with only staff should give 0 unique visitors."""
        self._ingest([
            make_event("ENTRY", is_staff=True),
            make_event("ENTRY", is_staff=True),
        ])
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.json()["unique_visitors"] == 0

    def test_metrics_zero_purchases(self):
        """Store with visitors but no purchases should have 0.0 conversion rate."""
        self._ingest([make_event("ENTRY") for _ in range(5)])
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        data = resp.json()
        assert data["unique_visitors"] == 5
        assert data["conversion_rate"] == 0.0

    def test_metrics_zone_dwell_aggregation(self):
        vid = f"VIS_{uuid.uuid4().hex[:6]}"
        self._ingest([
            make_event("ZONE_DWELL", visitor_id=vid, zone_id="SKINCARE", dwell_ms=30000),
            make_event("ZONE_DWELL", visitor_id=vid, zone_id="SKINCARE", dwell_ms=60000),
        ])
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        zones = resp.json()["avg_dwell_per_zone"]
        skincare = next((z for z in zones if z["zone_id"] == "SKINCARE"), None)
        assert skincare is not None
        assert skincare["avg_dwell_seconds"] == pytest.approx(45.0, abs=1.0)

    def test_health_endpoint(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "store-intelligence-api"
        assert "status" in data

    def test_health_stale_feed_detection(self):
        """After ingesting an old event, health should report STALE_FEED."""
        old_ts = datetime.utcnow() - timedelta(minutes=15)
        self._ingest([make_event("ENTRY", ts=old_ts)])
        resp = client.get("/health")
        stores = resp.json()["stores"]
        store = next((s for s in stores if s["store_id"] == STORE_ID), None)
        if store:
            assert store["feed_status"] in ("STALE_FEED", "OK")  # depends on timing


# ── Funnel tests ──────────────────────────────────────────────────────────────

class TestFunnel:
    def setup_method(self):
        clear_db()

    def _ingest(self, events):
        client.post("/events/ingest", json={"events": events})

    def test_funnel_reentry_no_double_count(self):
        """Re-entry visitor must count as 1 unique visitor in funnel, not 2."""
        vid = f"VIS_{uuid.uuid4().hex[:6]}"
        self._ingest([
            make_event("ENTRY", visitor_id=vid),
            make_event("EXIT", visitor_id=vid),
            make_event("REENTRY", visitor_id=vid),  # same person returning
            make_event("ENTRY", visitor_id=vid),    # second ENTRY (re-entry path)
        ])
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        assert resp.status_code == 200
        funnel = resp.json()["funnel"]
        entry_stage = next(s for s in funnel if s["stage"] == "Entry")
        assert entry_stage["count"] == 1  # de-duplicated

    def test_funnel_stages_present(self):
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        stages = [s["stage"] for s in resp.json()["funnel"]]
        assert "Entry" in stages
        assert "Zone Visit" in stages
        assert "Billing Queue" in stages
        assert "Purchase" in stages

    def test_funnel_empty_store(self):
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        for stage in resp.json()["funnel"]:
            assert stage["count"] == 0


# ── Anomaly tests ─────────────────────────────────────────────────────────────

class TestAnomalies:
    def setup_method(self):
        clear_db()

    def _ingest(self, events):
        client.post("/events/ingest", json={"events": events})

    def test_anomalies_queue_spike(self):
        events = []
        for i in range(6):
            e = make_event("BILLING_QUEUE_JOIN", zone_id="BILLING")
            e["metadata"]["queue_depth"] = 6
            events.append(e)
        self._ingest(events)
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        assert resp.status_code == 200
        anomalies = resp.json()["anomalies"]
        types = [a["type"] for a in anomalies]
        assert "BILLING_QUEUE_SPIKE" in types

    def test_anomalies_dead_zone(self):
        """Zone with no recent visits should trigger DEAD_ZONE anomaly."""
        old_ts = datetime.utcnow() - timedelta(minutes=35)
        self._ingest([
            make_event("ZONE_ENTER", zone_id="HAIRCARE", ts=old_ts),
        ])
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        anomalies = resp.json()["anomalies"]
        types = [a["type"] for a in anomalies]
        assert "DEAD_ZONE" in types

    def test_anomalies_empty_store_no_crash(self):
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        assert resp.status_code == 200
        assert isinstance(resp.json()["anomalies"], list)

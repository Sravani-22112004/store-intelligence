from sqlalchemy.orm import Session
from sqlalchemy import func, select, and_, distinct
from database import DBEvent, DBTransaction
from models import StoreAnomalies, Anomaly
from datetime import datetime, timedelta
from typing import List
import uuid
import logging

logger = logging.getLogger(__name__)

QUEUE_SPIKE_THRESHOLD = 5
DEAD_ZONE_MINUTES = 30
CONVERSION_DROP_THRESHOLD = 0.3  # 30% drop vs 7-day average


def get_store_anomalies(store_id: str, db: Session) -> StoreAnomalies:
    anomalies: List[Anomaly] = []
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ── 1. Queue spike ───────────────────────────────────────────────────────
    latest_queue_event = db.execute(
        select(DBEvent.metadata_json, DBEvent.timestamp)
        .where(
            and_(
                DBEvent.store_id == store_id,
                DBEvent.event_type == "BILLING_QUEUE_JOIN",
            )
        )
        .order_by(DBEvent.timestamp.desc())
        .limit(1)
    ).fetchone()

    if latest_queue_event:
        meta = latest_queue_event[0] or {}
        depth = meta.get("queue_depth", 0) or 0
        if depth >= QUEUE_SPIKE_THRESHOLD:
            severity = "CRITICAL" if depth >= 10 else "WARN"
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                type="BILLING_QUEUE_SPIKE",
                severity=severity,
                description=f"Billing queue depth is {depth} — above threshold of {QUEUE_SPIKE_THRESHOLD}",
                suggested_action="Deploy additional billing staff immediately. Consider opening express counter.",
                detected_at=latest_queue_event[1],
                zone_id="BILLING",
            ))

    # ── 2. Conversion drop vs 7-day average ─────────────────────────────────
    # Today's conversion rate
    today_visitors = db.execute(
        select(func.count(distinct(DBEvent.visitor_id)))
        .where(
            and_(
                DBEvent.store_id == store_id,
                DBEvent.is_staff == False,
                DBEvent.event_type == "ENTRY",
                DBEvent.timestamp >= today_start,
            )
        )
    ).scalar() or 0

    today_txns = db.execute(
        select(func.count(DBTransaction.id))
        .where(
            and_(
                DBTransaction.store_id == store_id,
                DBTransaction.timestamp >= today_start,
            )
        )
    ).scalar() or 0

    today_rate = today_txns / today_visitors if today_visitors > 0 else None

    # 7-day average
    seven_days_ago = today_start - timedelta(days=7)
    hist_visitors = db.execute(
        select(func.count(distinct(DBEvent.visitor_id)))
        .where(
            and_(
                DBEvent.store_id == store_id,
                DBEvent.is_staff == False,
                DBEvent.event_type == "ENTRY",
                DBEvent.timestamp >= seven_days_ago,
                DBEvent.timestamp < today_start,
            )
        )
    ).scalar() or 0

    hist_txns = db.execute(
        select(func.count(DBTransaction.id))
        .where(
            and_(
                DBTransaction.store_id == store_id,
                DBTransaction.timestamp >= seven_days_ago,
                DBTransaction.timestamp < today_start,
            )
        )
    ).scalar() or 0

    hist_rate = hist_txns / hist_visitors if hist_visitors > 0 else None

    if today_rate is not None and hist_rate and hist_rate > 0:
        drop = (hist_rate - today_rate) / hist_rate
        if drop >= CONVERSION_DROP_THRESHOLD:
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                type="CONVERSION_DROP",
                severity="WARN" if drop < 0.5 else "CRITICAL",
                description=f"Conversion rate today ({today_rate:.1%}) is {drop:.1%} below 7-day avg ({hist_rate:.1%})",
                suggested_action="Review staff deployment, check product availability, inspect for any customer experience issues.",
                detected_at=now,
            ))

    # ── 3. Dead zone — no visits in last 30 minutes ──────────────────────────
    cutoff = now - timedelta(minutes=DEAD_ZONE_MINUTES)
    recent_zones = set(
        row[0] for row in db.execute(
            select(distinct(DBEvent.zone_id))
            .where(
                and_(
                    DBEvent.store_id == store_id,
                    DBEvent.is_staff == False,
                    DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
                    DBEvent.timestamp >= cutoff,
                    DBEvent.zone_id.isnot(None),
                )
            )
        ).fetchall()
    )

    all_zones = set(
        row[0] for row in db.execute(
            select(distinct(DBEvent.zone_id))
            .where(
                and_(
                    DBEvent.store_id == store_id,
                    DBEvent.zone_id.isnot(None),
                )
            )
        ).fetchall()
    )

    dead_zones = all_zones - recent_zones
    for zone in dead_zones:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            type="DEAD_ZONE",
            severity="INFO",
            description=f"Zone '{zone}' has had no customer visits in the last {DEAD_ZONE_MINUTES} minutes",
            suggested_action=f"Check if zone '{zone}' display is attractive. Consider repositioning or promoting items in this zone.",
            detected_at=now,
            zone_id=zone,
        ))

    return StoreAnomalies(store_id=store_id, anomalies=anomalies)

from sqlalchemy.orm import Session
from sqlalchemy import func, select, and_, distinct
from database import DBEvent, DBTransaction
from models import StoreMetrics, ZoneDwell
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def get_date_range(db: Session, store_id: str):
    """Get the most recent date we have data for."""
    latest = db.execute(
        select(func.max(DBEvent.timestamp))
        .where(DBEvent.store_id == store_id)
    ).scalar()

    if not latest:
        # fallback to today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start, today_start + timedelta(days=1)

    day_start = latest.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


def get_store_metrics(store_id: str, db: Session) -> StoreMetrics:
    day_start, day_end = get_date_range(db, store_id)

    customer_filter = and_(
        DBEvent.store_id == store_id,
        DBEvent.is_staff == False,
        DBEvent.timestamp >= day_start,
        DBEvent.timestamp < day_end,
    )

    unique_visitors = db.execute(
        select(func.count(distinct(DBEvent.visitor_id)))
        .where(and_(customer_filter, DBEvent.event_type == "ENTRY"))
    ).scalar() or 0

    total_transactions = db.execute(
        select(func.count(DBTransaction.id))
        .where(and_(
            DBTransaction.store_id == store_id,
            DBTransaction.timestamp >= day_start,
            DBTransaction.timestamp < day_end,
        ))
    ).scalar() or 0

    conversion_rate = round(total_transactions / unique_visitors, 4) if unique_visitors > 0 else 0.0

    zone_dwells_raw = db.execute(
        select(
            DBEvent.zone_id,
            func.avg(DBEvent.dwell_ms).label("avg_dwell"),
            func.count(DBEvent.id).label("visit_count"),
        )
        .where(and_(
            customer_filter,
            DBEvent.event_type.in_(["ZONE_DWELL", "ZONE_ENTER"]),
            DBEvent.zone_id.isnot(None),
        ))
        .group_by(DBEvent.zone_id)
    ).fetchall()

    avg_dwell_per_zone = [
        ZoneDwell(
            zone_id=row.zone_id,
            avg_dwell_seconds=round((row.avg_dwell or 0) / 1000, 2),
            visit_count=row.visit_count,
        )
        for row in zone_dwells_raw
    ]

    latest_queue = db.execute(
        select(DBEvent.metadata_json)
        .where(and_(
            DBEvent.store_id == store_id,
            DBEvent.event_type == "BILLING_QUEUE_JOIN",
        ))
        .order_by(DBEvent.timestamp.desc())
        .limit(1)
    ).scalar()

    queue_depth = 0
    if latest_queue and isinstance(latest_queue, dict):
        queue_depth = latest_queue.get("queue_depth") or 0

    abandonments = db.execute(
        select(func.count(distinct(DBEvent.visitor_id)))
        .where(and_(customer_filter, DBEvent.event_type == "BILLING_QUEUE_ABANDON"))
    ).scalar() or 0

    billing_joins = db.execute(
        select(func.count(distinct(DBEvent.visitor_id)))
        .where(and_(customer_filter, DBEvent.event_type == "BILLING_QUEUE_JOIN"))
    ).scalar() or 0

    abandonment_rate = round(abandonments / billing_joins, 4) if billing_joins > 0 else 0.0

    return StoreMetrics(
        store_id=store_id,
        date=day_start.date().isoformat(),
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
        total_transactions=total_transactions,
    )

from sqlalchemy.orm import Session
from sqlalchemy import func, select, and_, distinct
from database import DBEvent
from models import StoreHeatmap, ZoneHeatmapEntry
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

MIN_SESSIONS_FOR_CONFIDENCE = 20


def get_date_range(db: Session, store_id: str):
    latest = db.execute(
        select(func.max(DBEvent.timestamp))
        .where(DBEvent.store_id == store_id)
    ).scalar()
    if not latest:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return today, today + timedelta(days=1)
    day_start = latest.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, day_start + timedelta(days=1)


def get_store_heatmap(store_id: str, db: Session) -> StoreHeatmap:
    day_start, day_end = get_date_range(db, store_id)

    base = and_(
        DBEvent.store_id == store_id,
        DBEvent.is_staff == False,
        DBEvent.timestamp >= day_start,
        DBEvent.timestamp < day_end,
        DBEvent.zone_id.isnot(None),
    )

    zone_stats = db.execute(
        select(
            DBEvent.zone_id,
            func.count(DBEvent.id).label("visit_count"),
            func.avg(DBEvent.dwell_ms).label("avg_dwell_ms"),
            func.count(distinct(DBEvent.visitor_id)).label("unique_visitors"),
        )
        .where(and_(base, DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"])))
        .group_by(DBEvent.zone_id)
    ).fetchall()

    if not zone_stats:
        return StoreHeatmap(store_id=store_id, zones=[])

    max_visits = max(row.visit_count for row in zone_stats) or 1

    total_sessions = db.execute(
        select(func.count(distinct(DBEvent.visitor_id)))
        .where(and_(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False,
            DBEvent.event_type == "ENTRY",
            DBEvent.timestamp >= day_start,
            DBEvent.timestamp < day_end,
        ))
    ).scalar() or 0

    zones = [
        ZoneHeatmapEntry(
            zone_id=row.zone_id,
            visit_frequency=row.visit_count,
            avg_dwell_seconds=round((row.avg_dwell_ms or 0) / 1000, 2),
            normalized_score=round((row.visit_count / max_visits) * 100, 2),
            data_confidence=total_sessions >= MIN_SESSIONS_FOR_CONFIDENCE,
        )
        for row in zone_stats
    ]

    zones.sort(key=lambda z: z.normalized_score, reverse=True)
    return StoreHeatmap(store_id=store_id, zones=zones)

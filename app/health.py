from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, distinct
from database import DBEvent, check_db_health
from models import HealthResponse, StoreHealth
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

STALE_FEED_THRESHOLD_MINUTES = 10


def get_health(db: Session) -> HealthResponse:
    db_ok = check_db_health()
    now = datetime.utcnow()

    stores = []

    if db_ok:
        # Get all known stores + their last event timestamp
        store_rows = db.execute(
            select(
                DBEvent.store_id,
                func.max(DBEvent.timestamp).label("last_event"),
            )
            .group_by(DBEvent.store_id)
        ).fetchall()

        for row in store_rows:
            last_ts = row.last_event
            lag_minutes = None
            feed_status = "NO_DATA"

            if last_ts:
                lag_minutes = round((now - last_ts).total_seconds() / 60, 2)
                feed_status = "STALE_FEED" if lag_minutes > STALE_FEED_THRESHOLD_MINUTES else "OK"

            stores.append(StoreHealth(
                store_id=row.store_id,
                status="ok",
                last_event_timestamp=last_ts,
                feed_status=feed_status,
                lag_minutes=lag_minutes,
            ))

    return HealthResponse(
        service="store-intelligence-api",
        status="ok" if db_ok else "degraded",
        stores=stores,
        timestamp=now,
    )

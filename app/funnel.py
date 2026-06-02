from sqlalchemy.orm import Session
from sqlalchemy import func, select, and_, distinct
from database import DBEvent, DBTransaction
from models import StoreFunnel, FunnelStage
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


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


def get_store_funnel(store_id: str, db: Session) -> StoreFunnel:
    day_start, day_end = get_date_range(db, store_id)

    base = and_(
        DBEvent.store_id == store_id,
        DBEvent.is_staff == False,
        DBEvent.timestamp >= day_start,
        DBEvent.timestamp < day_end,
    )

    entered_visitors = set(
        row[0] for row in db.execute(
            select(distinct(DBEvent.visitor_id))
            .where(and_(base, DBEvent.event_type == "ENTRY"))
        ).fetchall()
    )
    entry_count = len(entered_visitors)

    zone_visitors = set(
        row[0] for row in db.execute(
            select(distinct(DBEvent.visitor_id))
            .where(and_(base, DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"])))
        ).fetchall()
    )
    zone_count = len(zone_visitors & entered_visitors)

    billing_visitors = set(
        row[0] for row in db.execute(
            select(distinct(DBEvent.visitor_id))
            .where(and_(base, DBEvent.event_type == "BILLING_QUEUE_JOIN"))
        ).fetchall()
    )
    billing_count = len(billing_visitors & entered_visitors)

    transactions = db.execute(
        select(DBTransaction.timestamp)
        .where(and_(
            DBTransaction.store_id == store_id,
            DBTransaction.timestamp >= day_start,
            DBTransaction.timestamp < day_end,
        ))
    ).fetchall()

    converted_visitors = set()
    for txn in transactions:
        txn_time = txn[0]
        window_start = txn_time - timedelta(minutes=5)
        visitors_in_window = db.execute(
            select(distinct(DBEvent.visitor_id))
            .where(and_(
                DBEvent.store_id == store_id,
                DBEvent.is_staff == False,
                DBEvent.event_type.in_(["BILLING_QUEUE_JOIN", "ZONE_ENTER"]),
                DBEvent.zone_id.in_(["BILLING", "CHECKOUT", "BILLING_COUNTER"]),
                DBEvent.timestamp >= window_start,
                DBEvent.timestamp <= txn_time,
            ))
        ).fetchall()
        converted_visitors.update(row[0] for row in visitors_in_window)

    purchase_count = len(converted_visitors & entered_visitors)

    def drop_off(current, previous):
        if previous == 0:
            return 0.0
        return round((1 - current / previous) * 100, 2)

    funnel = [
        FunnelStage(stage="Entry", count=entry_count, drop_off_pct=0.0),
        FunnelStage(stage="Zone Visit", count=zone_count, drop_off_pct=drop_off(zone_count, entry_count)),
        FunnelStage(stage="Billing Queue", count=billing_count, drop_off_pct=drop_off(billing_count, zone_count)),
        FunnelStage(stage="Purchase", count=purchase_count, drop_off_pct=drop_off(purchase_count, billing_count)),
    ]

    return StoreFunnel(store_id=store_id, funnel=funnel)

from sqlalchemy.orm import Session
from sqlalchemy import select
from models import StoreEvent, EventBatch, IngestResponse
from database import DBEvent
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def ingest_events(batch: EventBatch, db: Session) -> IngestResponse:
    accepted = 0
    rejected = 0
    duplicate = 0
    errors = []

    # Fetch existing event_ids for dedup in one query
    incoming_ids = [e.event_id for e in batch.events]
    existing_ids = set(
        row[0] for row in db.execute(
            select(DBEvent.event_id).where(DBEvent.event_id.in_(incoming_ids))
        ).fetchall()
    )

    seen_in_batch = set()

    for event in batch.events:
        try:
            # Idempotency — skip duplicates
            if event.event_id in existing_ids or event.event_id in seen_in_batch:
                duplicate += 1
                continue

            seen_in_batch.add(event.event_id)

            db_event = DBEvent(
                event_id=event.event_id,
                store_id=event.store_id,
                camera_id=event.camera_id,
                visitor_id=event.visitor_id,
                event_type=event.event_type.value,
                timestamp=event.timestamp,
                zone_id=event.zone_id,
                dwell_ms=event.dwell_ms,
                is_staff=event.is_staff,
                confidence=event.confidence,
                metadata_json=event.metadata.model_dump(),
            )
            db.add(db_event)
            accepted += 1

        except Exception as e:
            logger.error(f"Failed to ingest event {event.event_id}: {e}")
            errors.append({"event_id": event.event_id, "error": str(e)})
            rejected += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Batch commit failed: {e}")
        raise

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors,
    )

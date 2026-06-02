"""
replay.py — Replay saved .jsonl events into the API at configurable speed.
Useful for testing the dashboard and API without running the full detection pipeline.

Usage:
    python replay.py --events ../data/events/store.jsonl \
                     --api-url http://localhost:8000 \
                     --speed 10
"""
import argparse
import json
import time
import requests
import logging
from datetime import datetime
from typing import List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_events(path: str) -> List[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    # Sort by timestamp
    events.sort(key=lambda e: e["timestamp"])
    return events


def send_batch(api_url: str, events: List[dict]) -> bool:
    try:
        resp = requests.post(
            f"{api_url}/events/ingest",
            json={"events": events},
            timeout=10,
        )
        data = resp.json()
        logger.info(f"Sent {len(events)} events → accepted={data.get('accepted')} dup={data.get('duplicate')}")
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def replay(events: List[dict], api_url: str, speed: float = 1.0, batch_size: int = 20):
    if not events:
        logger.warning("No events to replay")
        return

    logger.info(f"Replaying {len(events)} events at {speed}x speed")

    first_ts = datetime.fromisoformat(events[0]["timestamp"].replace("Z", "+00:00"))
    replay_start = time.time()

    batch = []
    last_event_time = first_ts

    for event in events:
        event_ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        elapsed_event = (event_ts - first_ts).total_seconds()
        elapsed_real = time.time() - replay_start
        target_real = elapsed_event / speed

        wait = target_real - elapsed_real
        if wait > 0:
            time.sleep(wait)

        batch.append(event)

        if len(batch) >= batch_size:
            send_batch(api_url, batch)
            batch.clear()

    if batch:
        send_batch(api_url, batch)

    logger.info("Replay complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay events into Store Intelligence API")
    parser.add_argument("--events", required=True, help="Path to .jsonl events file")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0 = real time, 10 = 10x faster)")
    parser.add_argument("--batch-size", type=int, default=20, help="Events per API call")
    args = parser.parse_args()

    events = load_events(args.events)
    replay(events, args.api_url, speed=args.speed, batch_size=args.batch_size)

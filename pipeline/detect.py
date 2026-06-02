"""
detect.py — Main detection pipeline.
Processes CCTV clips with YOLOv8 + IoU tracker → structured events.
"""
import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

from tracker import SimpleTracker, TrackState
from emit import EventEmitter, StoreEvent, write_events_jsonl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_layout(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def get_zone_for_bbox(bbox: np.ndarray, zones: List[dict]) -> Optional[str]:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for zone in zones:
        zb = zone.get("bbox")
        if not zb:
            continue
        x1, y1, x2, y2 = zb
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return zone["zone_id"]
    return None


def classify_staff(crop: np.ndarray) -> bool:
    """Detect staff uniform by purple/violet HSV hue dominance."""
    if crop is None or crop.size == 0:
        return False
    try:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([120, 50, 50]), np.array([160, 255, 255]))
        ratio = np.sum(mask > 0) / (crop.shape[0] * crop.shape[1] + 1e-6)
        return bool(ratio > 0.35)
    except Exception:
        return False


def process_clip(
    video_path: str,
    store_id: str,
    camera_id: str,
    layout: dict,
    output_path: str,
    api_url: Optional[str] = None,
    clip_start_time: Optional[datetime] = None,
    batch_size: int = 50,
) -> List[StoreEvent]:

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    logger.info(f"Video: {total_frames} frames @ {fps}fps ({frame_w}x{frame_h})")

    if clip_start_time is None:
        clip_start_time = datetime.utcnow()

    model = YOLO("yolov8n.pt")
    tracker = SimpleTracker()
    emitter = EventEmitter(store_id, camera_id)

    # Get zones for this camera from layout
    store_zones = []
    for store in layout.get("stores", []):
        if store.get("store_id") == store_id:
            for cam in store.get("cameras", []):
                if cam.get("camera_id") == camera_id:
                    store_zones = cam.get("zones", [])
                    break

    is_entry_camera = camera_id in ("CAM_1", "CAM_ENTRY_01") or "ENTRY" in camera_id.upper()
    entry_line_y = int(frame_h * 0.45)  # entry threshold line at 45% of frame height

    all_events: List[StoreEvent] = []
    pending_batch: List[StoreEvent] = []

    zone_enter_times: Dict[str, Tuple[str, datetime]] = {}
    last_zone: Dict[str, Optional[str]] = {}
    prev_centroids: Dict[int, np.ndarray] = {}
    entered_visitors: set = set()
    exited_visitors: set = set()
    known_track_ids: set = set()  # track ids we've already seen

    PROCESS_EVERY_N = 3
    DWELL_EMIT_INTERVAL_MS = 30_000

    frame_idx = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % PROCESS_EVERY_N != 0:
            continue

        frame_time = clip_start_time + timedelta(seconds=frame_idx / fps)

        results = model(frame, classes=[0], verbose=False, conf=0.4)
        detections = []

        for result in results:
            for box in result.boxes:
                bbox = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0])
                x1, y1, x2, y2 = [int(v) for v in bbox]
                crop = frame[max(0, y1):y2, max(0, x1):x2]
                is_staff = classify_staff(crop)
                detections.append((bbox, conf, is_staff))

        active_tracks, new_ids, reentry_ids = tracker.update(detections, frame_time)

        # Emit REENTRY
        for vid in reentry_ids:
            track = next((t for t in active_tracks if t.visitor_id == vid), None)
            if track:
                ev = emitter.reentry(vid, frame_time, track.confidence)
                all_events.append(ev)
                pending_batch.append(ev)

        for track in active_tracks:
            vid = track.visitor_id
            tid = track.track_id

            # ── Entry/Exit via line crossing (entry camera) ───────────────
            if is_entry_camera:
                prev_c = prev_centroids.get(tid)
                if prev_c is not None:
                    cy_prev = prev_c[1]
                    cy_curr = track.centroid[1]
                    # Crossed entry line moving downward = ENTRY
                    if cy_prev < entry_line_y <= cy_curr and vid not in entered_visitors:
                        entered_visitors.add(vid)
                        ev = emitter.entry(vid, frame_time, track.is_staff, track.confidence)
                        all_events.append(ev)
                        pending_batch.append(ev)
                        logger.debug(f"ENTRY: {vid} staff={track.is_staff}")
                    # Crossed entry line moving upward = EXIT
                    elif cy_prev > entry_line_y >= cy_curr and vid in entered_visitors and vid not in exited_visitors:
                        exited_visitors.add(vid)
                        ev = emitter.exit(vid, frame_time, track.is_staff, track.confidence)
                        all_events.append(ev)
                        pending_batch.append(ev)
                        logger.debug(f"EXIT: {vid}")

            # ── For non-entry cameras: emit ENTRY for every new track ─────
            # This ensures visitors detected on floor/billing cameras
            # are counted even if entry camera missed them
            elif tid not in known_track_ids:
                known_track_ids.add(tid)
                if vid not in entered_visitors:
                    entered_visitors.add(vid)
                    ev = emitter.entry(vid, frame_time, track.is_staff, track.confidence)
                    all_events.append(ev)
                    pending_batch.append(ev)

            prev_centroids[tid] = track.centroid.copy()

            # ── Zone detection ────────────────────────────────────────────
            if store_zones:
                current_zone = get_zone_for_bbox(track.bbox, store_zones)
                prev_zone = last_zone.get(vid)

                if current_zone != prev_zone:
                    if prev_zone and vid in zone_enter_times:
                        enter_zone, enter_time = zone_enter_times.pop(vid)
                        dwell_ms = int((frame_time - enter_time).total_seconds() * 1000)
                        ev = emitter.zone_exit(vid, frame_time, prev_zone, dwell_ms, track.is_staff, track.confidence)
                        all_events.append(ev)
                        pending_batch.append(ev)

                    if current_zone:
                        zone_enter_times[vid] = (current_zone, frame_time)
                        ev = emitter.zone_enter(vid, frame_time, current_zone, track.is_staff, track.confidence)
                        all_events.append(ev)
                        pending_batch.append(ev)

                        if "BILLING" in current_zone.upper():
                            queue_depth = sum(
                                1 for t in active_tracks
                                if t.zone_id and "BILLING" in str(t.zone_id).upper()
                                and not t.is_staff
                            )
                            if queue_depth > 0:
                                ev = emitter.billing_queue_join(vid, frame_time, queue_depth, track.confidence)
                                all_events.append(ev)
                                pending_batch.append(ev)

                    last_zone[vid] = current_zone
                    track.zone_id = current_zone

                elif current_zone and vid in zone_enter_times:
                    enter_zone, enter_time = zone_enter_times[vid]
                    dwell_ms = int((frame_time - enter_time).total_seconds() * 1000)
                    if dwell_ms >= DWELL_EMIT_INTERVAL_MS:
                        intervals = dwell_ms // DWELL_EMIT_INTERVAL_MS
                        prev_dwell = getattr(track, '_last_dwell_intervals', 0)
                        if intervals > prev_dwell:
                            track._last_dwell_intervals = intervals
                            ev = emitter.zone_dwell(vid, frame_time, current_zone, dwell_ms, track.is_staff, track.confidence)
                            all_events.append(ev)
                            pending_batch.append(ev)

        if api_url and len(pending_batch) >= batch_size:
            _send_batch(api_url, pending_batch)
            pending_batch.clear()

        if frame_idx % 150 == 0:
            logger.info(f"Frame {frame_idx}/{total_frames} | Active tracks: {len(active_tracks)} | Events: {len(all_events)}")

    # Flush remaining
    if api_url and pending_batch:
        _send_batch(api_url, pending_batch)

    cap.release()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    write_events_jsonl(all_events, output_path)
    logger.info(f"Done. {len(all_events)} events written to {output_path}")
    return all_events


def _send_batch(api_url: str, events: List[StoreEvent]):
    import requests
    payload = {"events": [e.to_dict() for e in events]}
    try:
        r = requests.post(f"{api_url}/events/ingest", json=payload, timeout=10)
        logger.info(f"Sent {len(events)} events → {r.status_code}")
    except Exception as e:
        logger.error(f"Failed to send batch: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--output", default="events.jsonl")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--clip-start", default=None)
    args = parser.parse_args()

    layout = load_layout(args.layout)
    start_time = datetime.fromisoformat(args.clip_start) if args.clip_start else None

    process_clip(
        video_path=args.video,
        store_id=args.store_id,
        camera_id=args.camera_id,
        layout=layout,
        output_path=args.output,
        api_url=args.api_url,
        clip_start_time=start_time,
    )

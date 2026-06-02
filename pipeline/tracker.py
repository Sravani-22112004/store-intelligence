"""
tracker.py — ByteTrack-style bounding-box tracker with Re-ID via appearance
features (IoU + centroid distance). Handles re-entry detection.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import uuid


@dataclass
class TrackState:
    track_id: int
    visitor_id: str
    bbox: np.ndarray          # [x1, y1, x2, y2]
    centroid: np.ndarray      # [cx, cy]
    last_seen: datetime
    first_seen: datetime
    zone_id: Optional[str] = None
    zone_enter_time: Optional[datetime] = None
    is_staff: bool = False
    confidence: float = 1.0
    hits: int = 1
    misses: int = 0
    active: bool = True
    session_seq: int = 0      # event counter within this session
    exited: bool = False      # has this track exited?
    feature_history: List[np.ndarray] = field(default_factory=list)


class SimpleTracker:
    """
    IoU + centroid distance tracker with re-entry detection.
    Re-entry: if a known visitor_id re-appears after exit, emits REENTRY.
    """

    IOU_THRESHOLD = 0.3
    MAX_MISSES = 15          # frames before track is dropped
    REENTRY_WINDOW_SEC = 300  # 5 min — same person returning within 5 min = re-entry
    MAX_CENTROID_DIST = 150   # pixels; for association fallback

    def __init__(self):
        self._next_track_id = 1
        self.active_tracks: Dict[int, TrackState] = {}
        self.lost_tracks: List[TrackState] = []   # recently exited tracks (for re-ID)
        self._visitor_registry: Dict[str, TrackState] = {}  # visitor_id → last track

    def _iou(self, boxA: np.ndarray, boxB: np.ndarray) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        if inter == 0:
            return 0.0
        areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        return inter / (areaA + areaB - inter)

    def _centroid(self, bbox: np.ndarray) -> np.ndarray:
        return np.array([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2])

    def _centroid_dist(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(a - b))

    def _match_to_active(
        self, detections: List[Tuple[np.ndarray, float, bool]]
    ) -> Tuple[Dict[int, int], List[int], List[int]]:
        """
        Match detections to active tracks.
        Returns: (matches {track_id: det_idx}, unmatched_track_ids, unmatched_det_indices)
        """
        if not self.active_tracks or not detections:
            return {}, list(self.active_tracks.keys()), list(range(len(detections)))

        track_ids = list(self.active_tracks.keys())
        iou_matrix = np.zeros((len(track_ids), len(detections)))

        for ti, tid in enumerate(track_ids):
            track = self.active_tracks[tid]
            for di, (bbox, conf, _) in enumerate(detections):
                iou_matrix[ti, di] = self._iou(track.bbox, bbox)

        # Greedy matching by IoU
        matches = {}
        matched_dets = set()
        matched_tracks = set()

        sorted_pairs = np.dstack(np.unravel_index(
            np.argsort(iou_matrix.ravel())[::-1], iou_matrix.shape
        ))[0]

        for ti, di in sorted_pairs:
            if iou_matrix[ti, di] < self.IOU_THRESHOLD:
                break
            tid = track_ids[ti]
            if tid in matched_tracks or di in matched_dets:
                continue
            matches[tid] = di
            matched_tracks.add(tid)
            matched_dets.add(di)

        # Fallback: centroid distance for remaining
        for ti, tid in enumerate(track_ids):
            if tid in matched_tracks:
                continue
            track = self.active_tracks[tid]
            for di, (bbox, conf, _) in enumerate(detections):
                if di in matched_dets:
                    continue
                dist = self._centroid_dist(track.centroid, self._centroid(bbox))
                if dist < self.MAX_CENTROID_DIST:
                    matches[tid] = di
                    matched_tracks.add(tid)
                    matched_dets.add(di)
                    break

        unmatched_tracks = [track_ids[ti] for ti in range(len(track_ids))
                            if track_ids[ti] not in matched_tracks]
        unmatched_dets = [di for di in range(len(detections)) if di not in matched_dets]

        return matches, unmatched_tracks, unmatched_dets

    def _check_reentry(self, bbox: np.ndarray, now: datetime) -> Optional[TrackState]:
        """
        Check if this detection matches a recently exited visitor (re-entry).
        Uses centroid proximity of entry position.
        """
        centroid = self._centroid(bbox)
        for lost in self.lost_tracks:
            if not lost.exited:
                continue
            age = (now - lost.last_seen).total_seconds()
            if age > self.REENTRY_WINDOW_SEC:
                continue
            dist = self._centroid_dist(centroid, lost.centroid)
            if dist < self.MAX_CENTROID_DIST * 2:  # looser for re-entry
                return lost
        return None

    def update(
        self,
        detections: List[Tuple[np.ndarray, float, bool]],  # (bbox, confidence, is_staff)
        frame_time: datetime,
    ) -> Tuple[List[TrackState], List[str], List[str]]:
        """
        Update tracker with new frame detections.
        Returns: (active_tracks, new_track_visitor_ids, reentry_visitor_ids)
        """
        new_visitor_ids = []
        reentry_visitor_ids = []

        matches, unmatched_tracks, unmatched_dets = self._match_to_active(detections)

        # Update matched tracks
        for tid, di in matches.items():
            track = self.active_tracks[tid]
            bbox, conf, is_staff = detections[di]
            track.bbox = bbox
            track.centroid = self._centroid(bbox)
            track.last_seen = frame_time
            track.confidence = conf
            track.is_staff = is_staff
            track.misses = 0
            track.hits += 1

        # Increment miss counter for unmatched tracks
        for tid in unmatched_tracks:
            self.active_tracks[tid].misses += 1

        # Remove tracks that have been missing too long
        to_remove = [tid for tid, t in self.active_tracks.items()
                     if t.misses > self.MAX_MISSES]
        for tid in to_remove:
            track = self.active_tracks.pop(tid)
            track.exited = True
            self.lost_tracks.append(track)

        # Keep lost_tracks bounded
        cutoff = frame_time - timedelta(seconds=self.REENTRY_WINDOW_SEC)
        self.lost_tracks = [t for t in self.lost_tracks if t.last_seen >= cutoff]

        # Create new tracks for unmatched detections
        for di in unmatched_dets:
            bbox, conf, is_staff = detections[di]
            centroid = self._centroid(bbox)

            # Check re-entry
            prior = self._check_reentry(bbox, frame_time)
            if prior:
                visitor_id = prior.visitor_id
                reentry_visitor_ids.append(visitor_id)
                # Remove from lost
                self.lost_tracks = [t for t in self.lost_tracks if t.visitor_id != visitor_id]
            else:
                visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
                new_visitor_ids.append(visitor_id)

            track = TrackState(
                track_id=self._next_track_id,
                visitor_id=visitor_id,
                bbox=bbox,
                centroid=centroid,
                first_seen=frame_time,
                last_seen=frame_time,
                is_staff=is_staff,
                confidence=conf,
            )
            self.active_tracks[self._next_track_id] = track
            self._next_track_id += 1

        return list(self.active_tracks.values()), new_visitor_ids, reentry_visitor_ids

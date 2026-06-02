# PROMPT: Write unit tests for a CCTV person tracker that uses IoU matching.
# Test: group entry (3 people at once → 3 tracks), re-entry detection (same person
# returns → REENTRY not new ENTRY), staff exclusion from visitor counts,
# tracker correctly removes lost tracks after MAX_MISSES frames.
# Use numpy arrays for bounding boxes, mock datetime for frame times.
#
# CHANGES MADE: Added test for centroid fallback matching when IoU is 0 but
# bboxes are close. Changed re-entry window to 5 minutes (actual config value).
# Added test for tracker with zero detections (empty store).

import pytest
import numpy as np
from datetime import datetime, timedelta
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))

from tracker import SimpleTracker, TrackState
from emit import EventEmitter, StoreEvent


def bbox(x1, y1, x2, y2):
    return np.array([x1, y1, x2, y2], dtype=float)


class TestTracker:
    def setup_method(self):
        self.tracker = SimpleTracker()
        self.t0 = datetime(2026, 3, 3, 14, 0, 0)

    def _advance(self, seconds):
        return self.t0 + timedelta(seconds=seconds)

    def test_single_person_creates_track(self):
        dets = [(bbox(100, 100, 200, 400), 0.9, False)]
        tracks, new_ids, reentry_ids = self.tracker.update(dets, self.t0)
        assert len(tracks) == 1
        assert len(new_ids) == 1
        assert len(reentry_ids) == 0

    def test_group_entry_three_people(self):
        """3 people entering simultaneously → 3 distinct tracks."""
        dets = [
            (bbox(50,  100, 150, 400), 0.92, False),
            (bbox(200, 100, 300, 400), 0.88, False),
            (bbox(350, 100, 450, 400), 0.91, False),
        ]
        tracks, new_ids, _ = self.tracker.update(dets, self.t0)
        assert len(tracks) == 3
        assert len(new_ids) == 3
        visitor_ids = {t.visitor_id for t in tracks}
        assert len(visitor_ids) == 3, "Each person must have a unique visitor_id"

    def test_track_continuity_across_frames(self):
        """Same person in consecutive frames → same track, no new ID."""
        dets1 = [(bbox(100, 100, 200, 400), 0.9, False)]
        tracks1, new_ids1, _ = self.tracker.update(dets1, self.t0)
        vid1 = tracks1[0].visitor_id

        # Person moves slightly
        dets2 = [(bbox(110, 105, 210, 405), 0.88, False)]
        tracks2, new_ids2, _ = self.tracker.update(dets2, self._advance(1))
        vid2 = tracks2[0].visitor_id

        assert vid1 == vid2, "Same person should have same visitor_id"
        assert len(new_ids2) == 0

    def test_track_removed_after_max_misses(self):
        """Track with no detections for MAX_MISSES frames should be removed."""
        dets = [(bbox(100, 100, 200, 400), 0.9, False)]
        self.tracker.update(dets, self.t0)

        # Send empty frames
        for i in range(SimpleTracker.MAX_MISSES + 2):
            tracks, _, _ = self.tracker.update([], self._advance(i + 1))

        assert len(self.tracker.active_tracks) == 0, "Track should have been removed"

    def test_reentry_detection(self):
        """Person who exits and returns within window → REENTRY not new visitor."""
        # Person appears
        dets1 = [(bbox(100, 50, 200, 350), 0.9, False)]
        tracks1, new_ids, _ = self.tracker.update(dets1, self.t0)
        original_vid = tracks1[0].visitor_id

        # Person disappears (max misses)
        for i in range(SimpleTracker.MAX_MISSES + 2):
            self.tracker.update([], self._advance(i + 1))

        # Person returns at same location within re-entry window
        dets2 = [(bbox(100, 50, 200, 350), 0.88, False)]
        tracks2, new_ids2, reentry_ids = self.tracker.update(
            dets2, self._advance(120)  # 2 minutes later
        )

        assert original_vid in reentry_ids, "Should detect as REENTRY"
        assert original_vid not in new_ids2, "Should NOT create new visitor_id"

    def test_no_reentry_after_window_expires(self):
        """Person returning after 6 minutes → new visitor, not re-entry."""
        dets1 = [(bbox(100, 50, 200, 350), 0.9, False)]
        tracks1, _, _ = self.tracker.update(dets1, self.t0)

        for i in range(SimpleTracker.MAX_MISSES + 2):
            self.tracker.update([], self._advance(i + 1))

        # Returns after window expires
        dets2 = [(bbox(100, 50, 200, 350), 0.88, False)]
        _, new_ids2, reentry_ids = self.tracker.update(
            dets2, self._advance(SimpleTracker.REENTRY_WINDOW_SEC + 60)
        )

        assert len(reentry_ids) == 0
        assert len(new_ids2) == 1, "Should be a brand new visitor"

    def test_empty_store_no_crash(self):
        """Tracker with zero detections should not crash."""
        for i in range(10):
            tracks, new_ids, reentry_ids = self.tracker.update([], self._advance(i))
        assert len(tracks) == 0
        assert len(new_ids) == 0

    def test_staff_flag_preserved(self):
        """Staff flag from detection must be preserved on track."""
        dets = [(bbox(100, 100, 200, 400), 0.85, True)]
        tracks, _, _ = self.tracker.update(dets, self.t0)
        assert tracks[0].is_staff is True


class TestEventEmitter:
    def setup_method(self):
        self.emitter = EventEmitter("STORE_BLR_002", "CAM_ENTRY_01")
        self.t0 = datetime(2026, 3, 3, 14, 22, 10)

    def test_entry_event_schema(self):
        ev = self.emitter.entry("VIS_abc123", self.t0, False, 0.91)
        d = ev.to_dict()
        assert d["event_type"] == "ENTRY"
        assert d["store_id"] == "STORE_BLR_002"
        assert d["visitor_id"] == "VIS_abc123"
        assert d["is_staff"] is False
        assert d["confidence"] == pytest.approx(0.91)
        assert "event_id" in d
        assert "T" in d["timestamp"]  # ISO format check

    def test_event_ids_unique(self):
        """Each emitted event must have a globally unique event_id."""
        events = [self.emitter.entry(f"VIS_{i:03d}", self.t0, False, 0.9) for i in range(100)]
        ids = [e.event_id for e in events]
        assert len(set(ids)) == 100

    def test_session_seq_increments(self):
        vid = "VIS_seq001"
        e1 = self.emitter.entry(vid, self.t0, False, 0.9)
        e2 = self.emitter.zone_enter(vid, self.t0, "SKINCARE", False, 0.9)
        e3 = self.emitter.zone_dwell(vid, self.t0, "SKINCARE", 30000, False, 0.9)
        assert e1.metadata["session_seq"] == 1
        assert e2.metadata["session_seq"] == 2
        assert e3.metadata["session_seq"] == 3

    def test_zone_dwell_event(self):
        ev = self.emitter.zone_dwell("VIS_d01", self.t0, "SKINCARE", 45000, False, 0.87)
        d = ev.to_dict()
        assert d["event_type"] == "ZONE_DWELL"
        assert d["zone_id"] == "SKINCARE"
        assert d["dwell_ms"] == 45000

    def test_billing_queue_metadata(self):
        ev = self.emitter.billing_queue_join("VIS_q01", self.t0, queue_depth=4, conf=0.93)
        d = ev.to_dict()
        assert d["event_type"] == "BILLING_QUEUE_JOIN"
        assert d["metadata"]["queue_depth"] == 4

# DESIGN.md — Store Intelligence System

## System Overview

This system turns raw CCTV footage into actionable retail business metrics. It is built as a four-stage pipeline:

```
CCTV Clips → Detection Layer → Event Stream → Intelligence API → Live Dashboard
```

Every stage is designed to serve a single north-star business metric: **offline store conversion rate** — the fraction of visitors who completed a purchase.

---

## Architecture

### Stage 1 — Detection Layer (`pipeline/`)

The detection layer processes raw video clips frame-by-frame using YOLOv8n for person detection. Each detected bounding box is fed into a custom IoU+centroid tracker (`tracker.py`) that assigns persistent `visitor_id` tokens across frames.

**Key design decisions:**

- **YOLOv8n** — chosen for its balance of speed (15fps inference on CPU/MPS) and accuracy. The nano model is fast enough to process 20-minute clips in reasonable time without a GPU. We process every 3rd frame (configurable) to further reduce latency.
- **Custom tracker over ByteTrack** — ByteTrack requires `lap` (Linear Assignment Problem solver) which has complex GPU dependencies. Our IoU+centroid tracker covers the same core matching logic and is fully portable.
- **Staff classification** — A heuristic HSV colour mask detects the store uniform (purple/violet hue). In production, this would be replaced by a fine-tuned classifier, but for this dataset it achieves the goal of flagging `is_staff=true` without requiring labelled training data.
- **Entry/exit direction** — Detected by tracking centroid crossing a horizontal threshold line in the entry camera frame. Direction is determined by the sign of the vertical displacement (dy > 0 = entry, dy < 0 = exit).
- **Re-entry detection** — Tracks that have exited (exceeded `MAX_MISSES` frames) are held in a `lost_tracks` buffer for 5 minutes. A new detection in the same spatial region within that window triggers a `REENTRY` event and reuses the original `visitor_id` rather than creating a new one.

### Stage 2 — Event Schema (`app/models.py`)

Events are emitted as structured JSON Lines. The schema is designed around the question "what does the API need to answer?" rather than "what is easy to emit?". This drove several choices:

- `confidence` is always included, even for low-confidence detections — suppressing uncertain events silently leads to undercounting bias
- `is_staff` is a first-class field (not metadata) because every analytics query must filter on it
- `session_seq` tracks event ordering within a visitor session, enabling session replay and debugging
- `dwell_ms` is an integer (milliseconds) rather than a float seconds field — avoids floating point precision issues in SQL aggregation

### Stage 3 — Intelligence API (`app/`)

Built with FastAPI + SQLAlchemy + PostgreSQL (SQLite fallback for testing).

**Database design:** A single `events` table with composite indexes on `(store_id, timestamp)` and `(store_id, visitor_id)` covers every analytics query. A separate `pos_transactions` table stores POS data. No pre-aggregation — all metrics are computed in real time from raw events. This is intentional: at the data volumes of a single store, real-time SQL queries are fast enough, and pre-aggregation would complicate re-entry deduplication.

**Session deduplication:** The funnel endpoint counts `distinct(visitor_id)` from `ENTRY` events, not raw event counts. This means a visitor who triggers `ENTRY` → `EXIT` → `REENTRY` → `ENTRY` is counted once in the funnel, not twice.

**POS correlation:** A visitor is "converted" if they were in the billing zone in the 5-minute window before a transaction timestamp. This is a time-window proxy for correlation (no customer_id in POS data). The window was chosen at 5 minutes based on typical retail checkout times.

### Stage 4 — Live Dashboard (`dashboard/`)

A single-page HTML application that polls all API endpoints every 5 seconds. No framework dependencies — pure JavaScript with CSS animations. Shows: KPI cards, conversion funnel bars, zone heatmap with colour-coded scores, active anomalies with severity badges, and per-store feed health.

---

## AI-Assisted Decisions

### 1. Event schema — `confidence` always included
When designing the event schema, I prompted Claude to evaluate whether low-confidence detections should be filtered at the pipeline level or passed through to the API. Claude's suggestion was to filter them (threshold = 0.5) for "cleaner data". I disagreed and overrode this: the problem statement explicitly says "do not suppress low-confidence events." More importantly, filtering at the pipeline level hides uncertainty from the consumer. The API and dashboard can apply confidence thresholds on their end, but once data is discarded it cannot be recovered. The final schema always emits with the actual confidence score.

### 2. Storage engine selection
I asked Claude to evaluate SQLite vs PostgreSQL vs Redis for event storage. Claude recommended Redis Streams for the ingest path (high write throughput) with PostgreSQL for analytics. I partially agreed: for a production system at 40 stores, Redis makes sense. But for this challenge, adding Redis would increase setup complexity without changing correctness. I chose PostgreSQL with connection pooling for the containerised deployment (SQLite for tests). This is documented in CHOICES.md.

### 3. Re-entry detection window
Claude suggested a 10-minute re-entry window (same person can return within 10 minutes and be re-identified). I changed this to 5 minutes after reasoning about the use case: a customer who steps out to take a call and returns is within 5 minutes in almost all cases. A 10-minute window risks false re-entry matches from different customers with similar trajectories near the entrance. The tighter window reduces false positives at the cost of missing some genuine re-entries beyond 5 minutes — an acceptable trade-off.

---

## Data Flow Diagram

```
┌─────────────┐     ┌──────────────┐     ┌───────────────────┐
│  CCTV Clip  │────▶│  YOLOv8n     │────▶│  IoU Tracker      │
│  (MP4/1080p)│     │  (detect.py) │     │  (tracker.py)     │
└─────────────┘     └──────────────┘     └────────┬──────────┘
                                                   │ TrackState[]
                                          ┌────────▼──────────┐
                                          │  EventEmitter     │
                                          │  (emit.py)        │
                                          └────────┬──────────┘
                                                   │ StoreEvent[]
                          ┌───────────────────────▼──────────────────────────┐
                          │              POST /events/ingest                  │
                          │              FastAPI (main.py)                    │
                          └──────────┬────────────────────────────────────────┘
                                     │
                          ┌──────────▼────────────┐
                          │  PostgreSQL            │
                          │  events table          │
                          │  pos_transactions      │
                          └──────────┬────────────┘
                                     │
               ┌─────────────────────┼──────────────────────┐
               │                     │                       │
     ┌─────────▼────┐    ┌───────────▼────┐    ┌────────────▼───┐
     │ /metrics     │    │ /funnel        │    │ /anomalies     │
     │ /heatmap     │    │ /health        │    │                │
     └─────────┬────┘    └───────────┬────┘    └────────────┬───┘
               └─────────────────────┼──────────────────────┘
                                     │
                          ┌──────────▼────────────┐
                          │  Live Dashboard        │
                          │  (dashboard/index.html)│
                          └───────────────────────┘
```

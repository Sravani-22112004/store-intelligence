# Store Intelligence System
### Purplle Tech Challenge 2026 — Round 2

An end-to-end pipeline from raw CCTV footage to live retail analytics.

**CCTV Clips → YOLOv8 Detection → Structured Events → FastAPI → Live Dashboard**

---

## Quick Start (5 commands)

```bash
# 1. Clone and enter the repo
git clone <your-repo-url> store-intelligence && cd store-intelligence

# 2. Place dataset files
#    Copy your clips directory and store_layout.json into data/
mkdir -p data/clips
cp /path/to/store_layout.json data/
cp /path/to/pos_transactions.csv data/

# 3. Start the API and database
docker compose up -d

# 4. Verify the API is running
curl http://localhost:8000/health

# 5. Open the live dashboard
open http://localhost:3000
```

The API is now live at **http://localhost:8000** and the dashboard at **http://localhost:3000**.

---

## Running the Detection Pipeline

### Install pipeline dependencies

```bash
cd pipeline
pip install -r requirements.txt
```

> YOLOv8 weights (`yolov8n.pt`) are downloaded automatically on first run (~6MB).

### Process a single clip

```bash
python detect.py \
  --video ../data/clips/STORE_BLR_002__CAM_ENTRY_01.mp4 \
  --store-id STORE_BLR_002 \
  --camera-id CAM_ENTRY_01 \
  --layout ../data/store_layout.json \
  --output ../data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl \
  --api-url http://localhost:8000 \
  --clip-start 2026-03-03T14:00:00
```

Events are written to the `.jsonl` file **and** streamed to the API in real time.

### Process all clips at once

```bash
cd pipeline
chmod +x run.sh
./run.sh ../data/clips ../data/store_layout.json http://localhost:8000
```

The script auto-detects `store_id` and `camera_id` from the filename.
Expected filename format: `STORE_BLR_002__CAM_ENTRY_01__2026-03-03T14-00-00.mp4`

### Replay events from a saved `.jsonl` file

```bash
# If you already have events and just want to feed them into the API:
python replay.py --events ../data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl \
                 --api-url http://localhost:8000 \
                 --speed 10   # 10x faster than real time
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/events/ingest` | Ingest batch of up to 500 events (idempotent) |
| `GET` | `/stores/{id}/metrics` | Real-time KPIs: visitors, conversion, dwell, queue |
| `GET` | `/stores/{id}/funnel` | Conversion funnel with drop-off percentages |
| `GET` | `/stores/{id}/heatmap` | Zone visit frequency, normalised 0–100 |
| `GET` | `/stores/{id}/anomalies` | Active anomalies with severity and suggested actions |
| `GET` | `/health` | Service status + per-store feed lag |

### Example: ingest an event

```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "store_id": "STORE_BLR_002",
      "camera_id": "CAM_ENTRY_01",
      "visitor_id": "VIS_c8a2f1",
      "event_type": "ENTRY",
      "timestamp": "2026-03-03T14:22:10Z",
      "zone_id": null,
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.91,
      "metadata": {"queue_depth": null, "sku_zone": null, "session_seq": 1}
    }]
  }'
```

### Example: get store metrics

```bash
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```

---

## Running Tests

```bash
cd store-intelligence

# Install test dependencies
pip install -r app/requirements.txt

# Run all tests with coverage
pytest tests/ -v --cov=app --cov-report=term-missing

# Run specific test file
pytest tests/test_metrics.py -v
pytest tests/test_pipeline.py -v
pytest tests/test_anomalies.py -v
```

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # Main detection script (YOLOv8 + tracker)
│   ├── tracker.py         # IoU+centroid tracker with re-entry detection
│   ├── emit.py            # Event schema builder and JSONL writer
│   ├── replay.py          # Replay saved events to API
│   ├── run.sh             # One-command: process all clips → events → API
│   └── requirements.txt
├── app/
│   ├── main.py            # FastAPI entrypoint, all routes
│   ├── models.py          # Pydantic schemas (event + response models)
│   ├── database.py        # SQLAlchemy ORM + connection management
│   ├── ingestion.py       # Ingest, dedup, batch commit
│   ├── metrics.py         # Real-time KPI computation
│   ├── funnel.py          # Conversion funnel + session deduplication
│   ├── heatmap.py         # Zone heatmap normalisation
│   ├── anomalies.py       # Queue spike, conversion drop, dead zone
│   ├── health.py          # Feed lag + service health
│   ├── logging_config.py  # Structured JSON logging + request middleware
│   ├── Dockerfile
│   └── requirements.txt
├── dashboard/
│   ├── index.html         # Live dashboard (polling, no framework)
│   └── Dockerfile
├── tests/
│   ├── test_metrics.py    # API ingestion + metrics tests
│   ├── test_pipeline.py   # Tracker + event emitter unit tests
│   └── test_anomalies.py  # Anomaly detection tests
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # 3 engineering decisions with full reasoning
├── data/                  # Place clips, layout JSON, POS CSV here (gitignored)
├── docker-compose.yml
└── README.md
```

---

## Live Dashboard

Open **http://localhost:3000** after `docker compose up`.

The dashboard polls all API endpoints every 5 seconds and shows:
- KPI cards (visitors, conversion rate, queue depth, abandonment rate)
- Feed health per store (OK / STALE_FEED / NO_DATA)
- Conversion funnel with animated bars
- Zone heatmap with colour-coded scores
- Active anomalies with severity badges (INFO / WARN / CRITICAL)

---

## Architecture Decisions

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full architecture overview and AI-assisted decision log.
See [`docs/CHOICES.md`](docs/CHOICES.md) for the three major engineering trade-offs.

---

## Notes

- Dataset files (clips, layout, POS CSV) are gitignored and must be placed in `data/` manually.
- The pipeline can run against clips offline and replay events into the API, or stream events in real time.
- SQLite is used for tests; PostgreSQL for the Docker deployment.

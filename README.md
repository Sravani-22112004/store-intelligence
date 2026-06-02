# Store Intelligence System
### Purplle Tech Challenge 2026 — Round 2

An end-to-end pipeline from raw CCTV footage to live retail analytics.

**CCTV Clips → YOLOv8 Detection → Structured Events → FastAPI → Live Dashboard**

---

## Quick Start (5 commands)

```bash
# 1. Clone the repo
git clone https://github.com/Sravani-22112004/store-intelligence.git
cd store-intelligence

# 2. Place the CCTV clips and POS data into data/
mkdir -p "data/clips/CCTV Footage"
# Copy your CAM_1.mp4 ... CAM_5.mp4 into data/clips/CCTV Footage/
# Copy your pos_transactions.csv into data/

# 3. Start the API + database + dashboard
docker compose up -d

# 4. Verify the API is live
curl http://localhost:8000/health

# 5. Open the live dashboard
open http://localhost:3000
```

API: **http://localhost:8000**
Dashboard: **http://localhost:3000**
API Docs: **http://localhost:8000/docs**

---

## Running the Detection Pipeline

```bash
# Install dependencies
cd pipeline
pip install -r requirements.txt

# Load POS transactions into the database
python3 load_pos.py \
  --csv ../data/pos_transactions.csv \
  --store-id STORE_BLR_002 \
  --db-url postgresql://store:store123@localhost:5432/storedb

# Process a single clip (streams events to API in real time)
python3 detect.py \
  --video "../data/clips/CCTV Footage/CAM_1.mp4" \
  --store-id STORE_BLR_002 \
  --camera-id CAM_1 \
  --layout ../data/store_layout.json \
  --output ../data/events/CAM_1.jsonl \
  --api-url http://localhost:8000 \
  --clip-start 2026-04-10T10:00:00

# Process all 5 clips at once
chmod +x run.sh
./run.sh "../data/clips/CCTV Footage" ../data/store_layout.json http://localhost:8000
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/events/ingest` | Ingest batch of up to 500 events (idempotent) |
| `GET` | `/stores/{id}/metrics` | Real-time KPIs: visitors, conversion, dwell, queue |
| `GET` | `/stores/{id}/funnel` | Conversion funnel with drop-off percentages |
| `GET` | `/stores/{id}/heatmap` | Zone visit frequency normalised 0–100 |
| `GET` | `/stores/{id}/anomalies` | Active anomalies with severity and suggested actions |
| `GET` | `/health` | Service status + per-store feed lag |

### Quick test
```bash
curl http://localhost:8000/stores/STORE_BLR_002/metrics
curl http://localhost:8000/stores/STORE_BLR_002/funnel
curl http://localhost:8000/stores/STORE_BLR_002/heatmap
curl http://localhost:8000/stores/STORE_BLR_002/anomalies
```

---

## Running Tests

```bash
pip install fastapi httpx pytest pytest-cov pytest-asyncio pydantic-settings sqlalchemy
pytest tests/ -v --cov=app --cov-report=term-missing
```

**Result: 38 passed, 88% coverage**

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # YOLOv8 + IoU tracker → structured events
│   ├── tracker.py         # Re-entry detection, group handling
│   ├── emit.py            # Event schema builder
│   ├── load_pos.py        # Load POS CSV into database
│   ├── replay.py          # Replay saved events to API
│   └── run.sh             # Process all clips in one command
├── app/
│   ├── main.py            # FastAPI entrypoint
│   ├── models.py          # Pydantic event + response schemas
│   ├── database.py        # SQLAlchemy ORM
│   ├── ingestion.py       # Ingest + deduplication
│   ├── metrics.py         # Real-time KPI computation
│   ├── funnel.py          # Conversion funnel + session dedup
│   ├── heatmap.py         # Zone heatmap
│   ├── anomalies.py       # Queue spike, conversion drop, dead zone
│   └── health.py          # Feed lag + service health
├── dashboard/
│   └── index.html         # Live dashboard (polls every 5s)
├── tests/
│   ├── test_metrics.py    # API + ingestion tests
│   ├── test_pipeline.py   # Tracker + emitter unit tests
│   └── test_anomalies.py  # Anomaly detection tests
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # 3 engineering decisions with reasoning
├── data/
│   └── store_layout.json  # Zone definitions for STORE_BLR_002
└── docker-compose.yml
```

---

## Architecture

```
CCTV Clips → YOLOv8n Detection → IoU Tracker → EventEmitter
                                                      ↓
                                          POST /events/ingest
                                                      ↓
                                              PostgreSQL
                                                      ↓
                          /metrics  /funnel  /heatmap  /anomalies  /health
                                                      ↓
                                          Live Dashboard (port 3000)
```

**Detection:** YOLOv8n (person class) + custom IoU+centroid tracker. Re-entry detection via 5-minute lost-track buffer. Staff classified by uniform colour (HSV). Entry/exit by centroid crossing a threshold line.

**API:** FastAPI + PostgreSQL. Real-time queries — no pre-aggregation. Session deduplication via distinct(visitor_id). POS correlation via 5-minute time window.

**Dashboard:** Single HTML file, polls all endpoints every 5 seconds.

---

## Key Design Decisions

See [`docs/DESIGN.md`](docs/DESIGN.md) and [`docs/CHOICES.md`](docs/CHOICES.md) for full reasoning.

- **YOLOv8n over YOLOv8m**: 3x faster on CPU, sufficient for 15fps footage
- **Custom tracker over ByteTrack**: No `lap` dependency issues on ARM Mac
- **PostgreSQL only, no Redis**: Correct for this scale; Redis documented as next step
- **Confidence always emitted**: Never suppress — consumer decides threshold
- **5-min re-entry window**: Tighter than AI suggested (10 min) to reduce false positives

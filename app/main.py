from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
import logging
import os

from database import get_db, create_tables, check_db_health
from models import (
    EventBatch, IngestResponse,
    StoreMetrics, StoreFunnel, StoreHeatmap, StoreAnomalies, HealthResponse
)
from ingestion import ingest_events
from metrics import get_store_metrics
from funnel import get_store_funnel
from heatmap import get_store_heatmap
from anomalies import get_store_anomalies
from health import get_health
from logging_config import setup_logging, RequestLoggingMiddleware

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV event streams",
    version="1.0.0",
)

# Allow all origins for dashboard access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)


@app.on_event("startup")
def startup():
    create_tables()
    logger.info("Store Intelligence API started")


@app.exception_handler(OperationalError)
async def db_error_handler(request: Request, exc: OperationalError):
    logger.error(f"Database error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "database_unavailable",
            "message": "The database is currently unavailable.",
            "trace_id": getattr(request.state, "trace_id", None),
        }
    )


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred.",
            "trace_id": getattr(request.state, "trace_id", None),
        }
    )


@app.post("/events/ingest", response_model=IngestResponse, status_code=200)
def ingest(batch: EventBatch, request: Request, db: Session = Depends(get_db)):
    logger.info("Ingesting events", extra={
        "trace_id": getattr(request.state, "trace_id", "-"),
        "event_count": len(batch.events),
        "endpoint": "POST /events/ingest",
    })
    return ingest_events(batch, db)


@app.get("/stores/{store_id}/metrics", response_model=StoreMetrics)
def metrics(store_id: str, db: Session = Depends(get_db)):
    return get_store_metrics(store_id, db)


@app.get("/stores/{store_id}/funnel", response_model=StoreFunnel)
def funnel(store_id: str, db: Session = Depends(get_db)):
    return get_store_funnel(store_id, db)


@app.get("/stores/{store_id}/heatmap", response_model=StoreHeatmap)
def heatmap(store_id: str, db: Session = Depends(get_db)):
    return get_store_heatmap(store_id, db)


@app.get("/stores/{store_id}/anomalies", response_model=StoreAnomalies)
def anomalies(store_id: str, db: Session = Depends(get_db)):
    return get_store_anomalies(store_id, db)


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    return get_health(db)

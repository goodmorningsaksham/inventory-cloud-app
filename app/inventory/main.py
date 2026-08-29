"""Inventory Service - Middle tier checking stock with retries to warehouse."""
import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed, retry_if_exception_type, RetryCallState
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

app = FastAPI(title="Inventory Service", version="1.0.0")

# Configuration (PR State: increased retries, lowered timeout, zero backoff)
WAREHOUSE_SERVICE_URL = os.getenv("WAREHOUSE_SERVICE_URL", "http://toxiproxy:18002")
RETRIES_MAX = int(os.getenv("RETRIES_MAX", "8"))
RETRY_TIMEOUT_SECONDS = float(os.getenv("RETRY_TIMEOUT_SECONDS", "0.5"))
RETRY_BACKOFF_FACTOR = float(os.getenv("RETRY_BACKOFF_FACTOR", "0.0"))

# Prometheus Metrics
INVENTORY_REQUESTS_TOTAL = Counter(
    "inventory_requests_total",
    "Total inventory reservation requests received",
    ["status"],
)
INVENTORY_LATENCY_SECONDS = Histogram(
    "inventory_request_duration_seconds",
    "Duration of inventory requests in seconds",
)
RETRY_COUNT_TOTAL = Counter(
    "retry_count_total",
    "Total retry attempts made to downstream warehouse",
    ["service", "target"],
)

RETRY_COUNT_TOTAL.labels(service="inventory", target="warehouse").inc(0)
INVENTORY_REQUESTS_TOTAL.labels(status="success").inc(0)

class CheckReserveRequest(BaseModel):
    item_id: str
    quantity: int
    order_id: Optional[str] = "ord_default"
    force_failure: Optional[bool] = False

class CheckReserveResponse(BaseModel):
    order_id: str
    item_id: str
    status: str
    reservation_id: Optional[str] = None
    retries_attempted: int = 0
    total_latency_ms: float

def record_retry_callback(retry_state: RetryCallState):
    if retry_state.attempt_number >= 1:
        RETRY_COUNT_TOTAL.labels(service="inventory", target="warehouse").inc()

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "inventory",
        "warehouse_url": WAREHOUSE_SERVICE_URL,
        "retries_max": RETRIES_MAX,
        "retry_timeout_s": RETRY_TIMEOUT_SECONDS,
        "retry_backoff_factor": RETRY_BACKOFF_FACTOR,
    }

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

def call_warehouse_with_retries(item_id: str, quantity: int, order_id: str, force_failure: bool = False) -> tuple[dict, int]:
    attempts = 0
    wait_strategy = (
        wait_exponential(multiplier=RETRY_BACKOFF_FACTOR, min=0.1, max=3.0)
        if RETRY_BACKOFF_FACTOR > 0
        else wait_fixed(0)
    )

    @retry(
        stop=stop_after_attempt(RETRIES_MAX),
        wait=wait_strategy,
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError)),
        before_sleep=record_retry_callback,
        reraise=True,
    )
    def _execute_http_call():
        nonlocal attempts
        attempts += 1
        with httpx.Client(timeout=RETRY_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{WAREHOUSE_SERVICE_URL}/reserve",
                json={
                    "item_id": item_id,
                    "quantity": quantity,
                    "order_id": order_id,
                    "force_failure": force_failure,
                },
            )
            resp.raise_for_status()
            return resp.json()

    data = _execute_http_call()
    return data, (attempts - 1)

@app.post("/check_and_reserve", response_model=CheckReserveResponse)
@app.post("/reserve", response_model=CheckReserveResponse)
def check_and_reserve(req: CheckReserveRequest):
    start_time = time.time()
    try:
        data, retries_made = call_warehouse_with_retries(
            item_id=req.item_id,
            quantity=req.quantity,
            order_id=req.order_id or "ord_default",
            force_failure=bool(req.force_failure),
        )
        duration = time.time() - start_time
        INVENTORY_LATENCY_SECONDS.observe(duration)
        INVENTORY_REQUESTS_TOTAL.labels(status="success").inc()
        return CheckReserveResponse(
            order_id=req.order_id or "ord_default",
            item_id=req.item_id,
            status="reserved",
            reservation_id=data.get("reservation_id"),
            retries_attempted=retries_made,
            total_latency_ms=round(duration * 1000, 2),
        )
    except Exception as e:
        INVENTORY_REQUESTS_TOTAL.labels(status="timeout_or_unreachable").inc()
        duration = time.time() - start_time
        INVENTORY_LATENCY_SECONDS.observe(duration)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Warehouse error: {str(e)}",
        )

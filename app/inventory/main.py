import os
import time
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed

app = FastAPI(title="inventory-service")

WAREHOUSE_URL = os.getenv("WAREHOUSE_URL", "http://warehouse-proxy:8475")
RETRIES_MAX = int(os.getenv("RETRIES_MAX", "3"))
RETRY_TIMEOUT_SECONDS = float(os.getenv("RETRY_TIMEOUT_SECONDS", "1.0"))
RETRY_BACKOFF_FACTOR = float(os.getenv("RETRY_BACKOFF_FACTOR", "0.5"))

RETRY_COUNTER = Counter(
    "retry_count_total",
    "Total retry attempts made by inventory service",
    ["service", "target"]
)
INVENTORY_REQUESTS = Counter(
    "inventory_requests_total",
    "Total inventory requests processed",
    ["service"]
)

def get_retry_decorator():
    if RETRY_BACKOFF_FACTOR > 0:
        wait_strategy = wait_exponential(multiplier=RETRY_BACKOFF_FACTOR, min=0.1, max=2.0)
    else:
        wait_strategy = wait_fixed(0)

    return retry(
        stop=stop_after_attempt(RETRIES_MAX),
        wait=wait_strategy,
        before_sleep=lambda retry_state: RETRY_COUNTER.labels(service="inventory", target="warehouse").inc(),
        reraise=True
    )

@app.get("/health")
def health():
    return {"status": "ok", "service": "inventory-service"}

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

class ReserveRequest(BaseModel):
    item_id: str
    quantity: int = 1

@app.post("/reserve")
def reserve(req: ReserveRequest):
    INVENTORY_REQUESTS.labels(service="inventory").inc()
    
    @get_retry_decorator()
    def call_warehouse():
        with httpx.Client(timeout=RETRY_TIMEOUT_SECONDS) as client:
            resp = client.post(f"{WAREHOUSE_URL}/check_stock", json={"item_id": req.item_id})
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="Warehouse error")
            return resp.json()

    try:
        data = call_warehouse()
        return {"status": "RESERVED", "item_id": req.item_id, "warehouse": data}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Warehouse unavailable: {str(e)}")

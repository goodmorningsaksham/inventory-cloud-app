"""Warehouse Service - Downstream inventory stock reservation provider."""
import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

app = FastAPI(title="Warehouse Service", version="1.0.0")

# Prometheus Metrics
WAREHOUSE_REQUESTS_TOTAL = Counter(
    "warehouse_requests_total",
    "Total warehouse reservation requests received",
    ["status"],
)
WAREHOUSE_LATENCY_SECONDS = Histogram(
    "warehouse_request_duration_seconds",
    "Duration of warehouse reservation handling in seconds",
)

class ReserveRequest(BaseModel):
    item_id: str
    quantity: int
    order_id: Optional[str] = "ord_default"
    force_failure: Optional[bool] = False

class ReserveResponse(BaseModel):
    reservation_id: str
    item_id: str
    quantity: int
    status: str

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "warehouse"}

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/reserve", response_model=ReserveResponse)
def reserve(req: ReserveRequest):
    start_time = time.time()
    
    if req.force_failure or req.quantity <= 0:
        WAREHOUSE_REQUESTS_TOTAL.labels(status="failed").inc()
        duration = time.time() - start_time
        WAREHOUSE_LATENCY_SECONDS.observe(duration)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stock reservation rejected",
        )

    WAREHOUSE_REQUESTS_TOTAL.labels(status="success").inc()
    duration = time.time() - start_time
    WAREHOUSE_LATENCY_SECONDS.observe(duration)
    
    res_id = f"res_{req.item_id}_{int(time.time() * 1000)}"
    return ReserveResponse(
        reservation_id=res_id,
        item_id=req.item_id,
        quantity=req.quantity,
        status="reserved",
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port)

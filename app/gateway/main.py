"""Gateway Service - Ingress API forwarding customer orders to Inventory."""
import os
import time
import uuid
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import httpx
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

app = FastAPI(title="Gateway Service", version="1.0.0")

# Configuration
INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8001")
GATEWAY_TIMEOUT_SECONDS = float(os.getenv("GATEWAY_TIMEOUT_SECONDS", "5.0"))

# Prometheus Metrics
GATEWAY_REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total ingress requests received at gateway",
    ["status"],
)
GATEWAY_LATENCY_SECONDS = Histogram(
    "gateway_request_duration_seconds",
    "Duration of gateway request handling in seconds",
)

class OrderRequest(BaseModel):
    item_id: str
    quantity: int
    order_id: Optional[str] = None
    force_failure: Optional[bool] = False

class OrderResponse(BaseModel):
    order_id: str
    status: str
    reservation_id: Optional[str] = None
    retries_attempted: int = 0
    total_latency_ms: float

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "gateway",
        "inventory_url": INVENTORY_SERVICE_URL,
    }

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/orders", response_model=OrderResponse)
def submit_order(order: OrderRequest):
    start_time = time.time()
    order_id = order.order_id or f"ord_{uuid.uuid4().hex[:8]}"
    
    inv_payload = {
        "item_id": order.item_id,
        "quantity": order.quantity,
        "order_id": order_id,
        "force_failure": order.force_failure,
    }
    
    try:
        with httpx.Client(timeout=GATEWAY_TIMEOUT_SECONDS) as client:
            resp = client.post(f"{INVENTORY_SERVICE_URL}/check_and_reserve", json=inv_payload)
            resp.raise_for_status()
            inv_data = resp.json()
            
        duration = time.time() - start_time
        GATEWAY_LATENCY_SECONDS.observe(duration)
        GATEWAY_REQUESTS_TOTAL.labels(status="success").inc()
        
        return OrderResponse(
            order_id=order_id,
            status=inv_data.get("status", "completed"),
            reservation_id=inv_data.get("reservation_id"),
            retries_attempted=inv_data.get("retries_attempted", 0),
            total_latency_ms=round(duration * 1000, 2),
        )
    except (httpx.RequestError, httpx.TimeoutException) as e:
        GATEWAY_REQUESTS_TOTAL.labels(status="timeout_or_unreachable").inc()
        duration = time.time() - start_time
        GATEWAY_LATENCY_SECONDS.observe(duration)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Inventory service unreachable or timed out: {str(e)}",
        )
    except Exception as e:
        GATEWAY_REQUESTS_TOTAL.labels(status="internal_error").inc()
        duration = time.time() - start_time
        GATEWAY_LATENCY_SECONDS.observe(duration)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gateway internal error: {str(e)}",
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

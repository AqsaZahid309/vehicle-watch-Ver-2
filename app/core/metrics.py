"""
Prometheus metrics. Exposed at GET /metrics (optionally protected by METRICS_TOKEN).

When the worker runs as a separate process it has its own registry; scrape it
via the API process for request/ingest metrics and via worker logs for cycles,
or run the worker in-process (RUN_WORKER_IN_API=true) to get everything in one place.
"""

import time

from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "vw_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "vw_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
TELEMETRY_INGESTED = Counter("vw_telemetry_ingested_total", "Telemetry readings stored")
ALERTS_CREATED = Counter(
    "vw_alerts_created_total", "Anomaly alerts created", ["severity", "fault_type"]
)
WORKER_CYCLE_SECONDS = Histogram(
    "vw_worker_cycle_seconds", "Background worker cycle duration",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
WORKER_LAST_SUCCESS = Gauge("vw_worker_last_success_timestamp", "Unix time of last successful cycle")
MODEL_RETRAINS = Counter("vw_model_retrains_total", "Anomaly model trainings", ["reason", "scope"])
LLM_REQUESTS = Counter("vw_llm_requests_total", "LLM summary requests", ["status"])
NOTIFICATIONS_SENT = Counter(
    "vw_notifications_total", "Notification delivery attempts", ["channel_type", "status"]
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        # Use the route template (/devices/{device_id}) not the raw path, to keep
        # label cardinality bounded.
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "unmatched"
        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
        return response

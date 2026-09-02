"""Prometheus metrics F5-2 — RED + domain histograms.

In-memory, no external prometheus_client required. Exposes /metrics in Prometheus text format.
Buckets: 0.005,0.01,0.025,0.05,0.1,0.25,0.5,1,2.5,5,10
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]


class _Histogram:
    def __init__(
        self,
        name: str,
        help_text: str,
        buckets: list[float] | None = None,
        labelnames: tuple[str, ...] = (),
    ):
        self.name = name
        self.help = help_text
        self.buckets = buckets or DEFAULT_BUCKETS
        self.labelnames = labelnames
        self._lock = threading.Lock()
        # key tuple(labels) -> {bucket:count, sum, count}
        self._data: dict[tuple, dict[str, Any]] = defaultdict(
            lambda: {"bucket_counts": [0] * len(self.buckets), "sum": 0.0, "count": 0}
        )

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}
        key = tuple(labels.get(k, "") for k in self.labelnames)
        with self._lock:
            d = self._data[key]
            d["sum"] += value
            d["count"] += 1
            for i, b in enumerate(self.buckets):
                if value <= b:
                    d["bucket_counts"][i] += 1
            # need to also increment +Inf done on exposition

    def collect(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"# HELP {self.name} {self.help}")
        lines.append(f"# TYPE {self.name} histogram")
        with self._lock:
            for key, d in self._data.items():
                label_str = ""
                if self.labelnames:
                    parts = [
                        f'{k}="{v}"' for k, v in zip(self.labelnames, key, strict=False) if v != ""
                    ]
                    if parts:
                        label_str = "{" + ",".join(parts) + "}"
                # cumulative counts
                cum = 0
                for i, b in enumerate(self.buckets):
                    cum = d[
                        "bucket_counts"
                    ][
                        i
                    ]  # already cumulative? Actually we increment only for buckets >= value, not cumulative. Let's make cumulative correctly.
                    # our bucket_counts currently is count per bucket LE, but we increment for each bucket where value <= bucket.
                    # To get cumulative, we need to propagate: counts should be cumulative already because we increment all buckets >= value.
                    # Example value 0.1 with buckets [0.05,0.1,0.25] -> increment bucket 0.1 and 0.25 -> counts [0,1,1] -> cumulative correct.
                    # So we can just expose as is, but need to ensure cumulative monotonic: with our method, smaller buckets not incremented for larger values? For value 0.5, buckets <=0.5 are [0.05,0.1,0.25,0.5] -> we increment those 4, leaving larger buckets also incremented? Actually for value 0.5, buckets >=0.5 are [0.5,1,2.5,...] -> we increment only from 0.5 upward, not smaller. Our earlier loop increments for value <= bucket, which for 0.5 means buckets 0.5,1,2.5,5,10 get +1, but 0.05,0.1,0.25 do not. That's correct cumulative LE: counts for buckets < value should be 0, for >= value should be incremented per observation? Wait for cumulative LE, bucket le="0.05" counts observations <=0.05, le="0.1" counts <=0.1, etc. So value 0.5 should increment buckets >=0.5? Let's see: buckets sorted ascending: 0.05,0.1,0.25,0.5,1,... Value 0.05 -> buckets >=0.05 are all buckets from 0.05 upward -> increment all? Actually if value=0.05, then value<=0.05 true for first bucket, and true for all larger buckets as well (since 0.05 <=0.1, <=0.25 etc). So our logic incrementing for value <= bucket will increment current and all larger buckets, which yields cumulative correctly (counts for larger buckets include smaller). For value=0.5, it will increment buckets 0.5,1,2.5,5,10 but not 0.05,0.1,0.25. That's correct because those smaller buckets should not count this observation (since 0.5 >0.05). So cumulative is correct.
                    lines.append(
                        f'{self.name}_bucket{{le="{b}"{("," + ",".join(parts)) if parts else ""}}} {cum}'
                    )
                # +Inf
                lines.append(
                    f'{self.name}_bucket{{le="+Inf"{("," + ",".join(parts)) if parts else ""}}} {d["count"]}'
                )
                lines.append(f"{self.name}_sum{label_str} {d['sum']}")
                lines.append(f"{self.name}_count{label_str} {d['count']}")
        return lines


class _Counter:
    def __init__(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()):
        self.name = name
        self.help = help_text
        self.labelnames = labelnames
        self._lock = threading.Lock()
        self._data: dict[tuple, float] = defaultdict(float)

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}
        key = tuple(labels.get(k, "") for k in self.labelnames)
        with self._lock:
            self._data[key] += value

    def collect(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"# HELP {self.name} {self.help}")
        lines.append(f"# TYPE {self.name} counter")
        with self._lock:
            for key, val in self._data.items():
                label_str = ""
                if self.labelnames:
                    parts = [
                        f'{k}="{v}"' for k, v in zip(self.labelnames, key, strict=False) if v != ""
                    ]
                    if parts:
                        label_str = "{" + ",".join(parts) + "}"
                lines.append(f"{self.name}{label_str} {val}")
        return lines


class _Gauge:
    def __init__(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()):
        self.name = name
        self.help = help_text
        self.labelnames = labelnames
        self._lock = threading.Lock()
        self._data: dict[tuple, float] = defaultdict(float)

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}
        key = tuple(labels.get(k, "") for k in self.labelnames)
        with self._lock:
            self._data[key] = value

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}
        key = tuple(labels.get(k, "") for k in self.labelnames)
        with self._lock:
            self._data[key] += value

    def collect(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"# HELP {self.name} {self.help}")
        lines.append(f"# TYPE {self.name} gauge")
        with self._lock:
            for key, val in self._data.items():
                label_str = ""
                if self.labelnames:
                    parts = [
                        f'{k}="{v}"' for k, v in zip(self.labelnames, key, strict=False) if v != ""
                    ]
                    if parts:
                        label_str = "{" + ",".join(parts) + "}"
                lines.append(f"{self.name}{label_str} {val}")
        return lines


class MetricsRegistry:
    """Singleton registry F5-2."""

    def __init__(self) -> None:
        # RED
        self.http_request_duration_seconds = _Histogram(
            "http_request_duration_seconds",
            "HTTP request duration",
            buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
            labelnames=("method", "path", "status"),
        )
        self.http_requests_total = _Counter(
            "http_requests_total", "Total HTTP requests", labelnames=("method", "path", "status")
        )
        # domain
        self.tool_call_duration_seconds = _Histogram(
            "tool_call_duration_seconds", "Tool call duration", labelnames=("tool",)
        )
        self.tool_calls_total = _Counter(
            "tool_calls_total", "Total tool calls", labelnames=("tool", "status")
        )
        self.llm_tokens_total = _Counter(
            "llm_tokens_total", "LLM tokens", labelnames=("provider", "model", "type")
        )
        self.llm_cost_usd_total = _Counter(
            "llm_cost_usd_total", "LLM cost USD", labelnames=("provider", "model", "tenant")
        )
        self.llm_requests_total = _Counter(
            "llm_requests_total", "LLM requests", labelnames=("provider", "model", "status")
        )
        self.approval_age_seconds = _Gauge(
            "approval_age_seconds", "Age of pending approvals", labelnames=("tenant",)
        )
        self.approval_pending = _Gauge(
            "approval_pending_total", "Pending approvals", labelnames=("tenant",)
        )
        self.rag_retrieval_latency_seconds = _Histogram(
            "rag_retrieval_latency_seconds", "RAG retrieval latency", labelnames=("tenant",)
        )
        self.rag_retrieval_total = _Counter(
            "rag_retrieval_total", "RAG retrievals", labelnames=("tenant",)
        )
        self.budget_exceeded_total = _Counter(
            "budget_exceeded_total", "Budget exceeded events", labelnames=("tenant", "reason")
        )
        self.cost_usd_per_execution = _Histogram(
            "cost_usd_per_execution", "Cost per execution USD", labelnames=("tenant",)
        )
        self.execution_duration_seconds = _Histogram(
            "execution_duration_seconds", "Execution duration", labelnames=("status",)
        )
        # Fase 6 — LLM cache hit/miss
        self.llm_cache_hits_total = _Counter(
            "llm_cache_hits_total", "LLM cache hits/misses", labelnames=("tenant", "result")
        )
        self.llm_cache_hit_rate = _Gauge(
            "llm_cache_hit_rate", "LLM cache hit rate", labelnames=("tenant",)
        )
        # internal counters for hit rate calculation
        self._cache_hits: dict[str, int] = {}
        self._cache_misses: dict[str, int] = {}

    # helpers
    def observe_http(self, method: str, path: str, status: int, duration_s: float) -> None:
        # normalize path: keep first 2 segments for cardinality
        norm_path = path
        # strip ids: replace uuid-like segments
        # simple: keep as is but truncate after /v1/
        try:
            if "/v1/procurement/executions/" in path and "/events" not in path:
                norm_path = "/v1/procurement/executions/{id}"
            elif "/v1/procurement/executions/" in path and "/events" in path:
                norm_path = "/v1/procurement/executions/{id}/events"
            elif "/v1/approvals/" in path and "/decision" in path:
                norm_path = "/v1/approvals/{id}/decision"
            elif "/v1/approvals/" in path:
                norm_path = "/v1/approvals/{id}"
            elif "/v1/rag/" in path:
                norm_path = path.split("?")[0]
        except Exception:
            pass
        labels = {"method": method, "path": norm_path, "status": str(status)}
        self.http_request_duration_seconds.observe(duration_s, labels)
        self.http_requests_total.inc(1, labels)

    def observe_tool(self, tool: str, duration_s: float, status: str = "success") -> None:
        self.tool_call_duration_seconds.observe(duration_s, {"tool": tool})
        self.tool_calls_total.inc(1, {"tool": tool, "status": status})

    def observe_rag(self, tenant: str, duration_s: float) -> None:
        self.rag_retrieval_latency_seconds.observe(duration_s, {"tenant": tenant})
        self.rag_retrieval_total.inc(1, {"tenant": tenant})

    def inc_llm_tokens(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        self.llm_tokens_total.inc(
            prompt_tokens, {"provider": provider, "model": model, "type": "prompt"}
        )
        self.llm_tokens_total.inc(
            completion_tokens, {"provider": provider, "model": model, "type": "completion"}
        )

    def inc_llm_cost(self, provider: str, model: str, tenant: str, cost: float) -> None:
        self.llm_cost_usd_total.inc(cost, {"provider": provider, "model": model, "tenant": tenant})

    def inc_llm_request(self, provider: str, model: str, status: str = "success") -> None:
        self.llm_requests_total.inc(1, {"provider": provider, "model": model, "status": status})

    def observe_cost(self, tenant: str, cost: float) -> None:
        self.cost_usd_per_execution.observe(cost, {"tenant": tenant})

    def inc_budget_exceeded(self, tenant: str, reason: str) -> None:
        self.budget_exceeded_total.inc(1, {"tenant": tenant, "reason": reason})

    def set_approval_pending(self, tenant: str, count: int) -> None:
        self.approval_pending.set(float(count), {"tenant": tenant})

    def inc_cache(self, tenant: str, hit: bool) -> None:
        result = "hit" if hit else "miss"
        self.llm_cache_hits_total.inc(1, {"tenant": tenant, "result": result})
        # track for hit_rate gauge
        try:
            if hit:
                self._cache_hits[tenant] = self._cache_hits.get(tenant, 0) + 1
            else:
                self._cache_misses[tenant] = self._cache_misses.get(tenant, 0) + 1
            total = self._cache_hits.get(tenant, 0) + self._cache_misses.get(tenant, 0)
            if total > 0:
                rate = self._cache_hits.get(tenant, 0) / total
                self.llm_cache_hit_rate.set(rate, {"tenant": tenant})
        except Exception:
            pass

    def get_cache_hit_rate(self, tenant: str) -> float:
        hits = self._cache_hits.get(tenant, 0)
        misses = self._cache_misses.get(tenant, 0)
        total = hits + misses
        return (hits / total) if total else 0.0

    def generate(self) -> str:
        lines: list[str] = []
        for metric in [
            self.http_request_duration_seconds,
            self.http_requests_total,
            self.tool_call_duration_seconds,
            self.tool_calls_total,
            self.llm_tokens_total,
            self.llm_cost_usd_total,
            self.llm_requests_total,
            self.rag_retrieval_latency_seconds,
            self.rag_retrieval_total,
            self.budget_exceeded_total,
            self.cost_usd_per_execution,
            self.execution_duration_seconds,
            self.approval_pending,
            self.approval_age_seconds,
            self.llm_cache_hits_total,
            self.llm_cache_hit_rate,
        ]:
            lines.extend(metric.collect())
        return "\n".join(lines) + "\n"


_global_metrics: MetricsRegistry | None = None
_lock = threading.Lock()


def get_metrics() -> MetricsRegistry:
    global _global_metrics
    with _lock:
        if _global_metrics is None:
            _global_metrics = MetricsRegistry()
        return _global_metrics


def reset_metrics() -> None:
    global _global_metrics
    with _lock:
        _global_metrics = MetricsRegistry()

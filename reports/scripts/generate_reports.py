#!/usr/bin/env python3
"""
gdnts CI Report Generator

Generates HTML and JUnit XML reports from Prometheus metrics.

Usage:
    python generate_reports.py [--prometheus-url URL] [--output-dir DIR] [--format FORMAT]
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.resources
import logging
import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

# Suppress urllib3 warning about LibreSSL on macOS (does not affect HTTP functionality)
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

from jinja2 import Template
from prometheus_api_client import PrometheusConnect

if TYPE_CHECKING:
    from collections.abc import Sequence

# Configure logging with verbose, human-readable format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclasses.dataclass(frozen=True)
class K6Stage:
    """A single stage in k6 test configuration."""

    duration: str  # e.g., "30s", "1m"
    target: int  # VU count


@dataclasses.dataclass(frozen=True)
class K6TestConfig:
    """Test configuration parsed from k6 script."""

    stages: list[K6Stage]
    total_duration: str  # e.g., "4m30s"
    peak_vus: int
    targets: list[str]  # e.g., ["foo.localhost", "bar.localhost"]
    thresholds: dict[str, str]  # e.g., {"p95": "<500ms", "error_rate": "<1%"}


@dataclasses.dataclass(frozen=True)
class K6Metrics:
    """k6 load test metrics from Prometheus."""

    total_requests: int
    request_rate: Decimal
    p95_latency_ms: Decimal
    avg_latency_ms: Decimal
    min_latency_ms: Decimal
    max_latency_ms: Decimal
    error_rate: Decimal
    failed_requests: int


@dataclasses.dataclass(frozen=True)
class PodResourceMetrics:
    """Per-pod resource metrics."""

    pod_name: str
    avg_cpu_millicores: Decimal
    max_cpu_millicores: Decimal
    avg_memory_mb: Decimal
    max_memory_mb: Decimal


@dataclasses.dataclass(frozen=True)
class CumulativeResourceMetrics:
    """Cumulative resource metrics for all pods."""

    avg_cpu_millicores: Decimal
    max_cpu_millicores: Decimal
    avg_memory_mb: Decimal
    max_memory_mb: Decimal


@dataclasses.dataclass(frozen=True)
class TimeSeriesPoint:
    """A single point in a time series."""

    timestamp: float
    value: float


@dataclasses.dataclass(frozen=True)
class TimeSeriesData:
    """Time series data for graphing."""

    cpu_points: list[TimeSeriesPoint]
    memory_points: list[TimeSeriesPoint]


@dataclasses.dataclass(frozen=True)
class PerPodTimeSeriesData:
    """Per-pod time series data for graphing with individual pod lines."""

    # Cumulative (sum of all pods)
    cumulative_cpu: list[TimeSeriesPoint]
    cumulative_memory: list[TimeSeriesPoint]
    # Per-pod data: {pod_name: [points]}
    per_pod_cpu: dict[str, list[TimeSeriesPoint]]
    per_pod_memory: dict[str, list[TimeSeriesPoint]]


@dataclasses.dataclass(frozen=True)
class VUStage:
    """VU stage with timestamps for graph markers."""

    start_timestamp: float
    end_timestamp: float
    vus: int


@dataclasses.dataclass(frozen=True)
class TestResult:
    """JUnit test case result."""

    name: str
    classname: str
    passed: bool
    actual_value: str
    threshold: str
    message: str = ""
    failure_type: str = ""


# =============================================================================
# K6 Script Parser
# =============================================================================


def parse_duration_to_seconds(duration_str: str) -> int:
    """Parse k6 duration string to seconds.

    Args:
        duration_str: Duration like "30s", "1m", "2m30s"

    Returns:
        Duration in seconds.
    """
    total_seconds = 0

    # Match minutes
    minutes_match = re.search(r"(\d+)m", duration_str)
    if minutes_match:
        total_seconds += int(minutes_match.group(1)) * 60

    # Match seconds
    seconds_match = re.search(r"(\d+)s", duration_str)
    if seconds_match:
        total_seconds += int(seconds_match.group(1))

    return total_seconds


def seconds_to_duration_str(seconds: int) -> str:
    """Convert seconds to human-readable duration string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Duration string like "4m30s".
    """
    minutes = seconds // 60
    remaining_seconds = seconds % 60

    if minutes > 0 and remaining_seconds > 0:
        return f"{minutes}m{remaining_seconds}s"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{remaining_seconds}s"


def parse_k6_script(script_path: Path) -> K6TestConfig:
    """Parse k6 script to extract test configuration.

    Args:
        script_path: Path to the k6 JavaScript file.

    Returns:
        K6TestConfig with parsed configuration.
    """
    logger.info("Parsing k6 script: %s", script_path)

    content = script_path.read_text(encoding="utf-8")

    # Extract stages array
    stages: list[K6Stage] = []
    stages_match = re.search(r"stages:\s*\[(.*?)\]", content, re.DOTALL)
    if stages_match:
        stages_content = stages_match.group(1)
        # Find all stage objects: { duration: '30s', target: 20 }
        stage_pattern = re.compile(
            r"\{\s*duration:\s*['\"]([^'\"]+)['\"],\s*target:\s*(\d+)\s*\}"
        )
        for match in stage_pattern.finditer(stages_content):
            stages.append(K6Stage(duration=match.group(1), target=int(match.group(2))))

    # Calculate total duration and peak VUs
    total_seconds = sum(parse_duration_to_seconds(s.duration) for s in stages)
    total_duration = seconds_to_duration_str(total_seconds)
    peak_vus = max((s.target for s in stages), default=0)

    # Extract hosts/targets
    targets: list[str] = []
    hosts_match = re.search(r"hosts\s*=\s*\[(.*?)\]", content, re.DOTALL)
    if hosts_match:
        hosts_content = hosts_match.group(1)
        # Find host values: { name: 'foo', host: 'foo.localhost', ... }
        host_pattern = re.compile(r"host:\s*['\"]([^'\"]+)['\"]")
        targets = [m.group(1) for m in host_pattern.finditer(hosts_content)]

    # Fallback: look for hardcoded host headers
    if not targets:
        header_pattern = re.compile(r"Host['\"]:\s*['\"]([^'\"]+\.localhost)['\"]")
        targets = list(set(m.group(1) for m in header_pattern.finditer(content)))
        targets.sort()

    # Extract thresholds
    thresholds: dict[str, str] = {}
    thresholds_match = re.search(r"thresholds:\s*\{(.*?)\}", content, re.DOTALL)
    if thresholds_match:
        thresholds_content = thresholds_match.group(1)
        # Find threshold values: 'http_req_duration': ['p(95)<500']
        threshold_pattern = re.compile(
            r"['\"]([^'\"]+)['\"]:\s*\[['\"]([^'\"]+)['\"]"
        )
        for match in threshold_pattern.finditer(thresholds_content):
            key = match.group(1)
            value = match.group(2)
            # Simplify key names for display
            if "duration" in key:
                thresholds["p95"] = value
            elif "failed" in key:
                thresholds["error_rate"] = value

    logger.info("  Stages: %d", len(stages))
    logger.info("  Total duration: %s", total_duration)
    logger.info("  Peak VUs: %d", peak_vus)
    logger.info("  Targets: %s", targets)
    logger.info("  Thresholds: %s", thresholds)

    return K6TestConfig(
        stages=stages,
        total_duration=total_duration,
        peak_vus=peak_vus,
        targets=targets,
        thresholds=thresholds,
    )


def calculate_vu_stages(
    test_start_time: datetime, stages: list[K6Stage]
) -> list[VUStage]:
    """Calculate VU stage time ranges for graph markers.

    Args:
        test_start_time: When the test started.
        stages: List of k6 stages.

    Returns:
        List of VUStage with timestamps for graph markers.
    """
    result: list[VUStage] = []
    current_time = test_start_time

    for stage in stages:
        duration_seconds = parse_duration_to_seconds(stage.duration)
        end_time = current_time + timedelta(seconds=duration_seconds)
        result.append(
            VUStage(
                start_timestamp=current_time.timestamp(),
                end_timestamp=end_time.timestamp(),
                vus=stage.target,
            )
        )
        current_time = end_time

    return result


# =============================================================================
# Metrics Collector
# =============================================================================


class MetricsCollector:
    """Collects metrics from Prometheus using prometheus-api-client."""

    def __init__(self, prometheus_url: str) -> None:
        """Initialize connection to Prometheus."""
        self._prom = PrometheusConnect(url=prometheus_url, disable_ssl=True)
        self._url = prometheus_url
        logger.info("Connected to Prometheus: %s", prometheus_url)

    def wait_for_k6_metrics(
        self, max_attempts: int = 30, interval: float = 2.0
    ) -> bool:
        """Wait for k6 metrics to appear in Prometheus."""
        logger.info("Waiting for k6 metrics in Prometheus...")
        for attempt in range(1, max_attempts + 1):
            result = self._query_scalar("sum(k6_http_reqs_total)")
            if result is not None and result > 0:
                logger.info("k6 metrics available (total requests: %s)", result)
                return True
            logger.info("  Attempt %d/%d: waiting for metrics...", attempt, max_attempts)
            time.sleep(interval)
        logger.warning("k6 metrics not found after %d attempts", max_attempts)
        return False

    def _query_scalar(self, promql: str) -> Decimal | None:
        """Execute PromQL query and return scalar result."""
        try:
            result = self._prom.custom_query(query=promql)
            if result and len(result) > 0:
                value = result[0]["value"][1]
                return Decimal(str(value))
        except Exception as e:
            logger.debug("Query '%s' failed: %s", promql, e)
        return None

    def _query_scalar_or_zero(self, promql: str) -> Decimal:
        """Execute PromQL query and return scalar result or zero."""
        result = self._query_scalar(promql)
        return result if result is not None else Decimal("0")

    def collect_k6_metrics(self) -> K6Metrics:
        """Collect k6 load test metrics from Prometheus."""
        logger.info("Collecting k6 metrics...")

        total_requests = int(self._query_scalar_or_zero("sum(k6_http_reqs_total)"))
        logger.info("  Total requests: %d", total_requests)

        request_rate = self._query_scalar_or_zero("sum(rate(k6_http_reqs_total[5m]))")
        logger.info("  Request rate: %.2f req/s", request_rate)

        p95_latency = self._query_scalar_or_zero(
            "histogram_quantile(0.95, sum(rate(k6_http_req_duration_seconds_bucket[5m])) by (le)) * 1000"
        )
        # Fallback to trend stats if histogram is empty
        if p95_latency == Decimal("0"):
            p95_latency = self._query_scalar_or_zero("k6_http_req_duration_p95 * 1000")
        logger.info("  P95 latency: %.2f ms", p95_latency)

        avg_latency = self._query_scalar_or_zero(
            "(sum(rate(k6_http_req_duration_seconds_sum[5m])) / "
            "sum(rate(k6_http_req_duration_seconds_count[5m]))) * 1000"
        )
        logger.info("  Avg latency: %.2f ms", avg_latency)

        min_latency = self._query_scalar_or_zero("k6_http_req_duration_min * 1000")
        max_latency = self._query_scalar_or_zero("k6_http_req_duration_max * 1000")
        logger.info("  Min/Max latency: %.2f / %.2f ms", min_latency, max_latency)

        failed_requests = int(
            self._query_scalar_or_zero("sum(k6_http_req_failed_total)")
        )
        error_rate = (
            Decimal(failed_requests) / Decimal(total_requests)
            if total_requests > 0
            else Decimal("0")
        )
        logger.info("  Error rate: %.4f%% (%d failed)", error_rate * 100, failed_requests)

        return K6Metrics(
            total_requests=total_requests,
            request_rate=request_rate,
            p95_latency_ms=p95_latency,
            avg_latency_ms=avg_latency,
            min_latency_ms=min_latency,
            max_latency_ms=max_latency,
            error_rate=error_rate,
            failed_requests=failed_requests,
        )

    def collect_pod_metrics(self) -> tuple[list[PodResourceMetrics], CumulativeResourceMetrics]:
        """Collect per-pod and cumulative resource metrics from Prometheus."""
        logger.info("Collecting per-pod resource metrics...")

        # Query per-pod CPU (millicores)
        cpu_query = (
            'sum by (pod) (rate(container_cpu_usage_seconds_total'
            '{namespace="default",pod=~"http-echo.*",container="http-echo"}[30s])) * 1000'
        )
        # Query per-pod memory (MB)
        mem_query = (
            'sum by (pod) (container_memory_working_set_bytes'
            '{namespace="default",pod=~"http-echo.*",container="http-echo"}) / 1048576'
        )

        try:
            cpu_results = self._prom.custom_query(query=cpu_query)
            mem_results = self._prom.custom_query(query=mem_query)
        except Exception as e:
            logger.warning("Failed to query pod metrics: %s", e)
            cpu_results = []
            mem_results = []

        # Build pod metrics from results
        pods: dict[str, dict[str, Decimal]] = {}

        for item in cpu_results:
            pod_name = item["metric"].get("pod", "unknown")
            cpu_value = Decimal(str(item["value"][1]))
            pods[pod_name] = {"cpu": cpu_value, "mem": Decimal("0")}

        for item in mem_results:
            pod_name = item["metric"].get("pod", "unknown")
            mem_value = Decimal(str(item["value"][1]))
            if pod_name in pods:
                pods[pod_name]["mem"] = mem_value
            else:
                pods[pod_name] = {"cpu": Decimal("0"), "mem": mem_value}

        pod_metrics = [
            PodResourceMetrics(
                pod_name=name,
                avg_cpu_millicores=data["cpu"],
                max_cpu_millicores=data["cpu"],
                avg_memory_mb=data["mem"],
                max_memory_mb=data["mem"],
            )
            for name, data in sorted(pods.items())
        ]

        # Calculate cumulative metrics
        total_cpu = sum(p.avg_cpu_millicores for p in pod_metrics)
        total_mem = sum(p.avg_memory_mb for p in pod_metrics)
        max_cpu = max((p.max_cpu_millicores for p in pod_metrics), default=Decimal("0"))
        max_mem = max((p.max_memory_mb for p in pod_metrics), default=Decimal("0"))

        cumulative = CumulativeResourceMetrics(
            avg_cpu_millicores=total_cpu,
            max_cpu_millicores=total_cpu,  # For instant query, avg == max
            avg_memory_mb=total_mem,
            max_memory_mb=total_mem,
        )

        logger.info("  Found %d pods with metrics", len(pod_metrics))
        logger.info("  Cumulative CPU: %.1f m, Memory: %.1f MB", total_cpu, total_mem)

        return pod_metrics, cumulative

    def collect_time_series(self, duration_minutes: int = 10) -> TimeSeriesData:
        """Collect time-series data for CPU and memory over the test duration.

        Args:
            duration_minutes: How far back to query (default 10 minutes).

        Returns:
            TimeSeriesData with CPU and memory time-series points.
        """
        logger.info("Collecting time-series data for graphs (last %d minutes)...", duration_minutes)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=duration_minutes)

        # Query CPU time series (sum across all http-echo pods, in millicores)
        cpu_query = (
            'sum(rate(container_cpu_usage_seconds_total'
            '{namespace="default",pod=~"http-echo.*",container="http-echo"}[30s])) * 1000'
        )

        # Query memory time series (sum across all http-echo pods, in MB)
        mem_query = (
            'sum(container_memory_working_set_bytes'
            '{namespace="default",pod=~"http-echo.*",container="http-echo"}) / 1048576'
        )

        cpu_points: list[TimeSeriesPoint] = []
        memory_points: list[TimeSeriesPoint] = []

        try:
            # Use query_range for time-series data
            cpu_result = self._prom.custom_query_range(
                query=cpu_query,
                start_time=start_time,
                end_time=end_time,
                step="15s",
            )

            mem_result = self._prom.custom_query_range(
                query=mem_query,
                start_time=start_time,
                end_time=end_time,
                step="15s",
            )

            # Parse CPU results
            if cpu_result and len(cpu_result) > 0:
                for ts, value in cpu_result[0].get("values", []):
                    try:
                        cpu_points.append(TimeSeriesPoint(timestamp=float(ts), value=float(value)))
                    except (ValueError, TypeError):
                        continue

            # Parse memory results
            if mem_result and len(mem_result) > 0:
                for ts, value in mem_result[0].get("values", []):
                    try:
                        memory_points.append(TimeSeriesPoint(timestamp=float(ts), value=float(value)))
                    except (ValueError, TypeError):
                        continue

        except Exception as e:
            logger.warning("Failed to collect time-series data: %s", e)

        logger.info("  CPU data points: %d", len(cpu_points))
        logger.info("  Memory data points: %d", len(memory_points))

        return TimeSeriesData(cpu_points=cpu_points, memory_points=memory_points)

    def get_k6_test_time_range(self) -> tuple[datetime, datetime] | None:
        """Get the actual time range of k6 test from Prometheus.

        Returns:
            Tuple of (start_time, end_time) or None if no k6 data found.
        """
        try:
            # Query the time range of k6_http_reqs_total
            # Use query_range to get actual data points and find min/max timestamps
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=30)  # Look back 30 minutes

            result = self._prom.custom_query_range(
                query="sum(rate(k6_http_reqs_total[30s]))",
                start_time=start_time,
                end_time=end_time,
                step="15s",
            )

            if result and len(result) > 0:
                values = result[0].get("values", [])
                # Find first and last non-zero values (actual test period)
                active_times = [
                    float(ts) for ts, val in values if float(val) > 0
                ]
                if active_times:
                    test_start = datetime.fromtimestamp(min(active_times), tz=timezone.utc)
                    test_end = datetime.fromtimestamp(max(active_times), tz=timezone.utc)
                    logger.info("Detected k6 test time range: %s to %s",
                               test_start.strftime("%H:%M:%S"),
                               test_end.strftime("%H:%M:%S"))
                    return (test_start, test_end)
        except Exception as e:
            logger.warning("Failed to detect k6 test time range: %s", e)

        return None

    def collect_per_pod_time_series(
        self, duration_minutes: int = 10
    ) -> PerPodTimeSeriesData:
        """Collect time-series data per pod for enhanced graphs.

        Args:
            duration_minutes: How far back to query (default 10 minutes).

        Returns:
            PerPodTimeSeriesData with cumulative and per-pod time series.
        """
        logger.info("Collecting per-pod time-series data (last %d minutes)...", duration_minutes)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=duration_minutes)

        # Query per-pod CPU (not summed, in millicores)
        cpu_query = (
            'rate(container_cpu_usage_seconds_total'
            '{namespace="default",pod=~"http-echo.*",container="http-echo"}[30s]) * 1000'
        )

        # Query per-pod memory (not summed, in MB)
        mem_query = (
            'container_memory_working_set_bytes'
            '{namespace="default",pod=~"http-echo.*",container="http-echo"} / 1048576'
        )

        per_pod_cpu: dict[str, list[TimeSeriesPoint]] = {}
        per_pod_memory: dict[str, list[TimeSeriesPoint]] = {}
        cumulative_cpu: list[TimeSeriesPoint] = []
        cumulative_memory: list[TimeSeriesPoint] = []

        try:
            # Query CPU time series
            cpu_result = self._prom.custom_query_range(
                query=cpu_query,
                start_time=start_time,
                end_time=end_time,
                step="15s",
            )

            # Query memory time series
            mem_result = self._prom.custom_query_range(
                query=mem_query,
                start_time=start_time,
                end_time=end_time,
                step="15s",
            )

            # Parse per-pod CPU results
            cpu_by_timestamp: dict[float, float] = {}
            if cpu_result:
                for series in cpu_result:
                    pod_name = series.get("metric", {}).get("pod", "unknown")
                    points: list[TimeSeriesPoint] = []
                    for ts, value in series.get("values", []):
                        try:
                            ts_float = float(ts)
                            val_float = float(value)
                            points.append(TimeSeriesPoint(timestamp=ts_float, value=val_float))
                            # Accumulate for cumulative
                            cpu_by_timestamp[ts_float] = cpu_by_timestamp.get(ts_float, 0) + val_float
                        except (ValueError, TypeError):
                            continue
                    if points:
                        per_pod_cpu[pod_name] = points

            # Build cumulative CPU
            cumulative_cpu = [
                TimeSeriesPoint(timestamp=ts, value=val)
                for ts, val in sorted(cpu_by_timestamp.items())
            ]

            # Parse per-pod memory results
            mem_by_timestamp: dict[float, float] = {}
            if mem_result:
                for series in mem_result:
                    pod_name = series.get("metric", {}).get("pod", "unknown")
                    points = []
                    for ts, value in series.get("values", []):
                        try:
                            ts_float = float(ts)
                            val_float = float(value)
                            points.append(TimeSeriesPoint(timestamp=ts_float, value=val_float))
                            # Accumulate for cumulative
                            mem_by_timestamp[ts_float] = mem_by_timestamp.get(ts_float, 0) + val_float
                        except (ValueError, TypeError):
                            continue
                    if points:
                        per_pod_memory[pod_name] = points

            # Build cumulative memory
            cumulative_memory = [
                TimeSeriesPoint(timestamp=ts, value=val)
                for ts, val in sorted(mem_by_timestamp.items())
            ]

        except Exception as e:
            logger.warning("Failed to collect per-pod time-series data: %s", e)

        logger.info("  Per-pod CPU series: %d pods", len(per_pod_cpu))
        logger.info("  Per-pod Memory series: %d pods", len(per_pod_memory))
        logger.info("  Cumulative CPU points: %d", len(cumulative_cpu))
        logger.info("  Cumulative Memory points: %d", len(cumulative_memory))

        return PerPodTimeSeriesData(
            cumulative_cpu=cumulative_cpu,
            cumulative_memory=cumulative_memory,
            per_pod_cpu=per_pod_cpu,
            per_pod_memory=per_pod_memory,
        )


# =============================================================================
# SVG Graph Generator (Enhanced)
# =============================================================================


class SVGGraphGenerator:
    """Generates SVG graphs with cumulative fill, per-pod lines, and VU markers."""

    # Graph dimensions (full width)
    WIDTH = 900
    HEIGHT = 320
    MARGIN_LEFT = 60
    MARGIN_RIGHT = 20
    MARGIN_TOP = 50
    MARGIN_BOTTOM = 50
    GRAPH_WIDTH = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    GRAPH_HEIGHT = HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    # Color palettes by deployment
    FOO_COLORS = ["#8b5cf6", "#a78bfa", "#c4b5fd"]  # Purple palette
    BAR_COLORS = ["#22c55e", "#4ade80", "#86efac"]  # Green palette
    CUMULATIVE_COLOR = "#3b82f6"  # Blue for cumulative
    CUMULATIVE_FILL = "#93c5fd"  # Light blue fill

    # VU stage background colors (alternating)
    VU_BG_COLORS = ["#fafafa", "#f5f5f5"]

    @classmethod
    def _round_to_nearest(cls, value: float, step: int = 10) -> int:
        """Round value up to nearest step."""
        return int((value + step - 1) // step * step)

    @classmethod
    def _get_pod_color(cls, pod_name: str, pod_index: int) -> str:
        """Get color for a pod based on its deployment name."""
        if "foo" in pod_name.lower():
            return cls.FOO_COLORS[pod_index % len(cls.FOO_COLORS)]
        elif "bar" in pod_name.lower():
            return cls.BAR_COLORS[pod_index % len(cls.BAR_COLORS)]
        else:
            # Default to purple palette
            return cls.FOO_COLORS[pod_index % len(cls.FOO_COLORS)]

    @classmethod
    def generate_resource_graph(
        cls,
        cumulative_points: list[TimeSeriesPoint],
        per_pod_data: dict[str, list[TimeSeriesPoint]],
        vu_stages: list[VUStage],
        title: str,
        y_label: str,
    ) -> str:
        """Generate resource graph with cumulative fill, per-pod lines, and VU markers.

        Args:
            cumulative_points: Cumulative time series data.
            per_pod_data: Per-pod time series data {pod_name: [points]}.
            vu_stages: VU stage information for background bands.
            title: Graph title.
            y_label: Y-axis label.

        Returns:
            SVG string.
        """
        if not cumulative_points:
            return cls._empty_graph(title, "No data available")

        # Calculate time range
        all_timestamps = [p.timestamp for p in cumulative_points]
        min_time = min(all_timestamps)
        max_time = max(all_timestamps)
        time_range = max_time - min_time if max_time > min_time else 1

        # Calculate value range with headroom, rounded to 10
        all_values = [p.value for p in cumulative_points]
        for points in per_pod_data.values():
            all_values.extend(p.value for p in points)
        max_value = max(all_values) if all_values else 10
        y_max = cls._round_to_nearest(max_value * 1.1, 10)
        if y_max == 0:
            y_max = 10

        # Helper to convert data to SVG coordinates
        def to_svg_x(timestamp: float) -> float:
            return cls.MARGIN_LEFT + ((timestamp - min_time) / time_range) * cls.GRAPH_WIDTH

        def to_svg_y(value: float) -> float:
            return cls.MARGIN_TOP + cls.GRAPH_HEIGHT - (value / y_max) * cls.GRAPH_HEIGHT

        # Start building SVG
        svg_parts = [
            f'<svg viewBox="0 0 {cls.WIDTH} {cls.HEIGHT}" xmlns="http://www.w3.org/2000/svg" ',
            f'style="width:100%;max-width:{cls.WIDTH}px;height:auto;font-family:-apple-system,BlinkMacSystemFont,sans-serif;">',
            f'<rect width="{cls.WIDTH}" height="{cls.HEIGHT}" fill="#ffffff" rx="12"/>',
        ]

        # Draw VU stage background bands
        for i, stage in enumerate(vu_stages):
            if stage.start_timestamp >= max_time or stage.end_timestamp <= min_time:
                continue
            # Clamp to visible range
            start_x = to_svg_x(max(stage.start_timestamp, min_time))
            end_x = to_svg_x(min(stage.end_timestamp, max_time))
            bg_color = cls.VU_BG_COLORS[i % len(cls.VU_BG_COLORS)]
            svg_parts.append(
                f'<rect x="{start_x:.1f}" y="{cls.MARGIN_TOP}" '
                f'width="{end_x - start_x:.1f}" height="{cls.GRAPH_HEIGHT}" '
                f'fill="{bg_color}"/>'
            )
            # Add VU label at top of band
            mid_x = (start_x + end_x) / 2
            svg_parts.append(
                f'<text x="{mid_x:.1f}" y="{cls.MARGIN_TOP - 8}" '
                f'text-anchor="middle" font-size="10" fill="#888">{stage.vus} VUs</text>'
            )

        # Draw grid lines
        num_y_lines = 5
        for i in range(num_y_lines + 1):
            y_val = (i / num_y_lines) * y_max
            y_pos = to_svg_y(y_val)
            svg_parts.append(
                f'<line x1="{cls.MARGIN_LEFT}" y1="{y_pos:.1f}" '
                f'x2="{cls.WIDTH - cls.MARGIN_RIGHT}" y2="{y_pos:.1f}" '
                f'stroke="#e5e5e5" stroke-width="1"/>'
            )
            svg_parts.append(
                f'<text x="{cls.MARGIN_LEFT - 8}" y="{y_pos + 4:.1f}" '
                f'text-anchor="end" font-size="11" fill="#888">{int(y_val)}</text>'
            )

        # Draw cumulative area with fill
        if cumulative_points:
            path_data = f"M {to_svg_x(cumulative_points[0].timestamp):.1f},{to_svg_y(cumulative_points[0].value):.1f}"
            for pt in cumulative_points[1:]:
                path_data += f" L {to_svg_x(pt.timestamp):.1f},{to_svg_y(pt.value):.1f}"
            # Close path for fill
            fill_path = path_data + (
                f" L {to_svg_x(cumulative_points[-1].timestamp):.1f},{cls.MARGIN_TOP + cls.GRAPH_HEIGHT:.1f}"
                f" L {to_svg_x(cumulative_points[0].timestamp):.1f},{cls.MARGIN_TOP + cls.GRAPH_HEIGHT:.1f} Z"
            )
            # Fill area
            svg_parts.append(
                f'<path d="{fill_path}" fill="{cls.CUMULATIVE_FILL}" fill-opacity="0.5"/>'
            )
            # Line
            svg_parts.append(
                f'<path d="{path_data}" fill="none" stroke="{cls.CUMULATIVE_COLOR}" stroke-width="2.5"/>'
            )

        # Draw per-pod lines (no fill, grouped by deployment color)
        foo_pods = sorted([p for p in per_pod_data.keys() if "foo" in p.lower()])
        bar_pods = sorted([p for p in per_pod_data.keys() if "bar" in p.lower()])
        other_pods = sorted([p for p in per_pod_data.keys() if p not in foo_pods and p not in bar_pods])

        pod_groups = [
            (foo_pods, cls.FOO_COLORS),
            (bar_pods, cls.BAR_COLORS),
            (other_pods, cls.FOO_COLORS),
        ]

        for pods, colors in pod_groups:
            for i, pod_name in enumerate(pods):
                points = per_pod_data.get(pod_name, [])
                if not points:
                    continue
                color = colors[i % len(colors)]
                path_data = f"M {to_svg_x(points[0].timestamp):.1f},{to_svg_y(points[0].value):.1f}"
                for pt in points[1:]:
                    path_data += f" L {to_svg_x(pt.timestamp):.1f},{to_svg_y(pt.value):.1f}"
                svg_parts.append(
                    f'<path d="{path_data}" fill="none" stroke="{color}" stroke-width="1.5" stroke-opacity="0.7"/>'
                )

        # Draw axes
        svg_parts.append(
            f'<line x1="{cls.MARGIN_LEFT}" y1="{cls.MARGIN_TOP}" '
            f'x2="{cls.MARGIN_LEFT}" y2="{cls.MARGIN_TOP + cls.GRAPH_HEIGHT}" '
            f'stroke="#1a1a1a" stroke-width="1"/>'
        )
        svg_parts.append(
            f'<line x1="{cls.MARGIN_LEFT}" y1="{cls.MARGIN_TOP + cls.GRAPH_HEIGHT}" '
            f'x2="{cls.WIDTH - cls.MARGIN_RIGHT}" y2="{cls.MARGIN_TOP + cls.GRAPH_HEIGHT}" '
            f'stroke="#1a1a1a" stroke-width="1"/>'
        )

        # Title
        svg_parts.append(
            f'<text x="{cls.WIDTH / 2}" y="24" text-anchor="middle" '
            f'font-size="16" font-weight="600" fill="#1a1a1a">{title}</text>'
        )

        # Y-axis label
        svg_parts.append(
            f'<text x="14" y="{cls.HEIGHT / 2}" text-anchor="middle" '
            f'font-size="12" fill="#666" transform="rotate(-90 14,{cls.HEIGHT / 2})">{y_label}</text>'
        )

        # X-axis time labels
        num_x_labels = 6
        for i in range(num_x_labels + 1):
            ts = min_time + (i / num_x_labels) * time_range
            x_pos = to_svg_x(ts)
            time_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
            svg_parts.append(
                f'<text x="{x_pos:.1f}" y="{cls.MARGIN_TOP + cls.GRAPH_HEIGHT + 20}" '
                f'text-anchor="middle" font-size="11" fill="#888">{time_str}</text>'
            )

        # Legend
        legend_y = cls.HEIGHT - 15
        legend_items = [("Cumulative", cls.CUMULATIVE_COLOR)]
        if foo_pods:
            legend_items.append(("foo pods", cls.FOO_COLORS[0]))
        if bar_pods:
            legend_items.append(("bar pods", cls.BAR_COLORS[0]))

        legend_x = cls.MARGIN_LEFT
        for label, color in legend_items:
            svg_parts.append(
                f'<rect x="{legend_x}" y="{legend_y - 8}" width="12" height="12" fill="{color}" rx="2"/>'
            )
            svg_parts.append(
                f'<text x="{legend_x + 16}" y="{legend_y}" font-size="11" fill="#666">{label}</text>'
            )
            legend_x += len(label) * 7 + 40

        svg_parts.append("</svg>")
        return "".join(svg_parts)

    @classmethod
    def _empty_graph(cls, title: str, message: str) -> str:
        """Generate an empty graph with a message."""
        return f'''<svg viewBox="0 0 {cls.WIDTH} {cls.HEIGHT}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{cls.WIDTH}px;height:auto;">
  <rect width="{cls.WIDTH}" height="{cls.HEIGHT}" fill="#fafafa" rx="12"/>
  <text x="{cls.WIDTH / 2}" y="30" text-anchor="middle" font-size="14" font-weight="600" fill="#1a1a1a">{title}</text>
  <rect x="40" y="50" width="{cls.WIDTH - 80}" height="{cls.HEIGHT - 80}" fill="#ffffff" stroke="#e5e5e5" rx="8"/>
  <text x="{cls.WIDTH / 2}" y="{cls.HEIGHT / 2}" text-anchor="middle" font-size="13" fill="#888">{message}</text>
</svg>'''

    @classmethod
    def generate_cpu_graph(
        cls,
        per_pod_data: PerPodTimeSeriesData,
        vu_stages: list[VUStage],
    ) -> str:
        """Generate CPU usage graph."""
        return cls.generate_resource_graph(
            cumulative_points=per_pod_data.cumulative_cpu,
            per_pod_data=per_pod_data.per_pod_cpu,
            vu_stages=vu_stages,
            title="CPU Usage Over Time",
            y_label="CPU (millicores)",
        )

    @classmethod
    def generate_memory_graph(
        cls,
        per_pod_data: PerPodTimeSeriesData,
        vu_stages: list[VUStage],
    ) -> str:
        """Generate memory usage graph."""
        return cls.generate_resource_graph(
            cumulative_points=per_pod_data.cumulative_memory,
            per_pod_data=per_pod_data.per_pod_memory,
            vu_stages=vu_stages,
            title="Memory Usage Over Time",
            y_label="Memory (MB)",
        )


# =============================================================================
# HTML Report Generator
# =============================================================================

# Template file name (loaded from package resources)
TEMPLATE_FILENAME = "report.html.template"


def _load_template_from_package() -> str:
    """Load the HTML template from package resources using importlib.resources.

    This works correctly for both:
    - Development: running from source directory
    - Production: installed package in site-packages

    Returns:
        Template content as string.

    Raises:
        FileNotFoundError: If template file is not found in package.
    """
    try:
        # Python 3.9+ compatible way to read package resources
        files = importlib.resources.files("scripts")
        template_file = files.joinpath(TEMPLATE_FILENAME)
        template_content = template_file.read_text(encoding="utf-8")
        return template_content
    except (FileNotFoundError, TypeError) as e:
        raise FileNotFoundError(
            f"HTML template not found in package: {TEMPLATE_FILENAME}\n"
            "Ensure the template file exists at: reports/scripts/report.html.template\n"
            "And pyproject.toml includes: [tool.setuptools.package-data] scripts = ['*.template']"
        ) from e


class HTMLReportGenerator:
    """Generates HTML load test report using Jinja2 templates."""

    def __init__(self, output_dir: Path, template_path: Path | None = None) -> None:
        """Initialize HTML report generator.

        Args:
            output_dir: Directory to write the generated report.
            template_path: Optional path to override the template file location.
                          If None, loads from package resources.
        """
        self._output_dir = output_dir
        self._template_path = template_path
        self._template = self._load_template()

    def _load_template(self) -> Template:
        """Load the HTML template."""
        if self._template_path is not None:
            # User provided explicit path - load from filesystem
            logger.info("Loading HTML template from path: %s", self._template_path)
            if not self._template_path.exists():
                raise FileNotFoundError(
                    f"HTML template not found: {self._template_path}"
                )
            template_content = self._template_path.read_text(encoding="utf-8")
        else:
            # Load from package resources (works for both dev and installed)
            logger.info("Loading HTML template from package resources: %s", TEMPLATE_FILENAME)
            template_content = _load_template_from_package()

        logger.info("Template loaded successfully (%d bytes)", len(template_content))
        return Template(template_content)

    def generate(
        self,
        k6_metrics: K6Metrics,
        pod_metrics: list[PodResourceMetrics],
        cumulative: CumulativeResourceMetrics,
        per_pod_time_series: PerPodTimeSeriesData | None = None,
        vu_stages: list[VUStage] | None = None,
        test_config: K6TestConfig | None = None,
    ) -> Path:
        """Generate HTML report and return output path."""
        logger.info("Generating HTML report...")

        # Determine status colors
        p95_status = "success" if k6_metrics.p95_latency_ms < 500 else "danger"
        error_status = "success" if k6_metrics.error_rate < Decimal("0.01") else "danger"

        # Generate timestamp
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%B %d, %Y at %H:%M UTC")
        timestamp_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Generate SVG graphs
        vu_stages_for_graph = vu_stages or []
        if per_pod_time_series is not None:
            cpu_graph = SVGGraphGenerator.generate_cpu_graph(per_pod_time_series, vu_stages_for_graph)
            memory_graph = SVGGraphGenerator.generate_memory_graph(per_pod_time_series, vu_stages_for_graph)
        else:
            cpu_graph = SVGGraphGenerator._empty_graph("CPU Usage Over Time", "No data available")
            memory_graph = SVGGraphGenerator._empty_graph("Memory Usage Over Time", "No data available")

        # Render template
        html_content = self._template.render(
            k6=k6_metrics,
            pods=pod_metrics,
            cumulative=cumulative,
            p95_status=p95_status,
            error_status=error_status,
            timestamp=timestamp,
            timestamp_iso=timestamp_iso,
            cpu_graph=cpu_graph,
            memory_graph=memory_graph,
            test_config=test_config,
        )

        output_path = self._output_dir / "report.html"
        output_path.write_text(html_content, encoding="utf-8")

        logger.info("HTML report generated: %s", output_path)
        return output_path


# =============================================================================
# JUnit Report Generator
# =============================================================================


class JUnitReportGenerator:
    """Generates JUnit XML test report."""

    THRESHOLDS = {
        "p95_latency_ms": Decimal("500"),
        "avg_latency_ms": Decimal("200"),
        "error_rate": Decimal("0.01"),
        "min_requests": 100,
    }

    def __init__(self, output_dir: Path) -> None:
        """Initialize JUnit report generator."""
        self._output_dir = output_dir

    def generate(self, k6_metrics: K6Metrics) -> Path:
        """Generate JUnit XML report and return output path."""
        logger.info("Generating JUnit report...")

        results = self._evaluate_tests(k6_metrics)
        output_path = self._output_dir / "junit.xml"
        self._write_junit_xml(results, k6_metrics, output_path)

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        logger.info("JUnit report generated: %s", output_path)
        logger.info("  Tests: %d passed, %d failed", passed, failed)

        return output_path

    def _evaluate_tests(self, metrics: K6Metrics) -> list[TestResult]:
        """Evaluate test thresholds against k6 metrics."""
        return [
            TestResult(
                name="P95 Latency",
                classname="LoadTest.Performance",
                passed=metrics.p95_latency_ms <= self.THRESHOLDS["p95_latency_ms"],
                actual_value=f"{metrics.p95_latency_ms:.2f}ms",
                threshold=f"<{self.THRESHOLDS['p95_latency_ms']}ms",
                message=f"P95 latency {metrics.p95_latency_ms:.2f}ms exceeds threshold {self.THRESHOLDS['p95_latency_ms']}ms",
                failure_type="PerformanceError",
            ),
            TestResult(
                name="Error Rate",
                classname="LoadTest.Reliability",
                passed=metrics.error_rate <= self.THRESHOLDS["error_rate"],
                actual_value=f"{metrics.error_rate * 100:.4f}%",
                threshold=f"<{self.THRESHOLDS['error_rate'] * 100}%",
                message=f"Error rate {metrics.error_rate * 100:.4f}% exceeds threshold 1%",
                failure_type="ReliabilityError",
            ),
            TestResult(
                name="Service Availability",
                classname="LoadTest.Availability",
                passed=metrics.total_requests >= self.THRESHOLDS["min_requests"],
                actual_value=str(metrics.total_requests),
                threshold=f">={self.THRESHOLDS['min_requests']}",
                message=f"Insufficient requests completed: {metrics.total_requests}",
                failure_type="AvailabilityError",
            ),
            TestResult(
                name="Average Response Time",
                classname="LoadTest.Performance",
                passed=metrics.avg_latency_ms <= self.THRESHOLDS["avg_latency_ms"],
                actual_value=f"{metrics.avg_latency_ms:.2f}ms",
                threshold=f"<{self.THRESHOLDS['avg_latency_ms']}ms",
                message=f"Average latency {metrics.avg_latency_ms:.2f}ms exceeds threshold {self.THRESHOLDS['avg_latency_ms']}ms",
                failure_type="PerformanceWarning",
            ),
        ]

    def _write_junit_xml(
        self,
        results: list[TestResult],
        metrics: K6Metrics,
        output_path: Path,
    ) -> None:
        """Write JUnit XML file using xml.etree.ElementTree."""
        failures = sum(1 for r in results if not r.passed)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build XML tree
        testsuites = ET.Element(
            "testsuites",
            name="gdnts CI Load Tests",
            tests=str(len(results)),
            failures=str(failures),
            errors="0",
            time="300",
            timestamp=timestamp,
        )

        testsuite = ET.SubElement(
            testsuites,
            "testsuite",
            name="Performance Tests",
            tests=str(len(results)),
            failures=str(failures),
            errors="0",
            time="300",
            timestamp=timestamp,
        )

        # Properties
        properties = ET.SubElement(testsuite, "properties")
        props = {
            "data_source": "Prometheus",
            "total_requests": str(metrics.total_requests),
            "p95_latency_ms": f"{metrics.p95_latency_ms:.2f}",
            "avg_latency_ms": f"{metrics.avg_latency_ms:.2f}",
            "error_rate": f"{metrics.error_rate:.6f}",
            "p95_threshold_ms": str(self.THRESHOLDS["p95_latency_ms"]),
            "avg_threshold_ms": str(self.THRESHOLDS["avg_latency_ms"]),
            "error_threshold": str(self.THRESHOLDS["error_rate"]),
            "min_requests_threshold": str(self.THRESHOLDS["min_requests"]),
        }
        for name, value in props.items():
            ET.SubElement(properties, "property", name=name, value=value)

        # Test cases
        for result in results:
            testcase = ET.SubElement(
                testsuite,
                "testcase",
                name=result.name,
                classname=result.classname,
                time="0",
            )
            if not result.passed:
                failure = ET.SubElement(
                    testcase,
                    "failure",
                    message=result.message,
                    type=result.failure_type,
                )
                failure.text = (
                    f"\nExpected: {result.threshold}\n"
                    f"Actual: {result.actual_value}\n"
                    f"Data Source: Prometheus\n"
                )

        # System output
        system_out = ET.SubElement(testsuite, "system-out")
        system_out.text = f"""
Load Test Summary (Data Source: Prometheus)
============================================
Total Requests: {metrics.total_requests}
Average Latency: {metrics.avg_latency_ms:.2f}ms
P95 Latency: {metrics.p95_latency_ms:.2f}ms
Error Rate: {metrics.error_rate * 100:.4f}%

Thresholds:
- P95 Latency: < {self.THRESHOLDS['p95_latency_ms']}ms
- Error Rate: < {self.THRESHOLDS['error_rate'] * 100}%
- Min Requests: {self.THRESHOLDS['min_requests']}
- Avg Latency: < {self.THRESHOLDS['avg_latency_ms']}ms

Test Results:
- P95 Latency: {'PASS' if results[0].passed else 'FAIL'}
- Error Rate: {'PASS' if results[1].passed else 'FAIL'}
- Service Availability: {'PASS' if results[2].passed else 'FAIL'}
- Average Response Time: {'PASS' if results[3].passed else 'FAIL'}
"""

        # Write XML file
        tree = ET.ElementTree(testsuites)
        ET.indent(tree, space="  ")
        tree.write(output_path, encoding="unicode", xml_declaration=True)


# =============================================================================
# Main Entry Point
# =============================================================================


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate HTML and JUnit reports from Prometheus metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://prometheus.localhost:8080",
        help="Prometheus API URL (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./reports/output"),
        help="Output directory for reports (default: %(default)s)",
    )
    parser.add_argument(
        "--format",
        choices=["html", "junit", "all"],
        default="all",
        help="Report format to generate (default: %(default)s)",
    )
    parser.add_argument(
        "--k6-script",
        type=Path,
        default=Path("./loadtest/scripts/load.js"),
        help="Path to k6 test script for dynamic configuration (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    logger.info("=" * 60)
    logger.info("gdnts CI Report Generator")
    logger.info("=" * 60)
    logger.info("Prometheus URL: %s", args.prometheus_url)
    logger.info("Output directory: %s", args.output_dir)
    logger.info("Report format: %s", args.format)
    logger.info("k6 script: %s", args.k6_script)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Parse k6 script for dynamic test configuration
        test_config: K6TestConfig | None = None
        if args.k6_script.exists():
            test_config = parse_k6_script(args.k6_script)
        else:
            logger.warning("k6 script not found: %s", args.k6_script)

        collector = MetricsCollector(args.prometheus_url)
        collector.wait_for_k6_metrics()

        k6_metrics = collector.collect_k6_metrics()
        pod_metrics, cumulative = collector.collect_pod_metrics()
        per_pod_time_series = collector.collect_per_pod_time_series()

        # Calculate VU stages aligned with actual k6 test time from Prometheus
        vu_stages: list[VUStage] = []
        if test_config:
            # Try to get actual test time range from k6 metrics in Prometheus
            k6_time_range = collector.get_k6_test_time_range()

            if k6_time_range:
                # Use actual k6 test start time
                test_start_time, _ = k6_time_range
                vu_stages = calculate_vu_stages(test_start_time, test_config.stages)
                logger.info("VU stages aligned to k6 test start: %s",
                           test_start_time.strftime("%H:%M:%S"))
            elif per_pod_time_series.cumulative_cpu:
                # Fallback: estimate from time series data end
                test_duration_seconds = sum(
                    parse_duration_to_seconds(s.duration) for s in test_config.stages
                )
                actual_end = max(p.timestamp for p in per_pod_time_series.cumulative_cpu)
                test_start_ts = actual_end - test_duration_seconds
                vu_start_time = datetime.fromtimestamp(test_start_ts, tz=timezone.utc)
                vu_stages = calculate_vu_stages(vu_start_time, test_config.stages)
                logger.info("VU stages aligned to estimated test start: %s",
                           vu_start_time.strftime("%H:%M:%S"))

        if args.format in ("html", "all"):
            HTMLReportGenerator(args.output_dir).generate(
                k6_metrics,
                pod_metrics,
                cumulative,
                per_pod_time_series,
                vu_stages,
                test_config,
            )

        if args.format in ("junit", "all"):
            JUnitReportGenerator(args.output_dir).generate(k6_metrics)

        logger.info("=" * 60)
        logger.info("Report generation complete!")
        logger.info("=" * 60)
        return 0

    except Exception as e:
        logger.exception("Report generation failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
ookla-speedtest-exporter
Prometheus metrics exporter for Ookla Speedtest CLI.

Usage:
  exporter.py            Start the HTTP metrics server (on_demand or cached mode)
  exporter.py --run-once Run one speedtest, update the cache file, then exit (cached mode / cron)
"""

import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from prometheus_client import start_http_server, REGISTRY
from prometheus_client.core import GaugeMetricFamily

# ── Configuration ─────────────────────────────────────────────────────────────

SCRAPE_MODE  = os.environ.get("SCRAPE_MODE", "on_demand").strip().lower()
SERVER_ID    = os.environ.get("SERVER_ID", "").strip()
CACHE_PATH   = Path("/tmp/speedtest_cache.json")
CACHE_TMP    = Path("/tmp/speedtest_cache.json.tmp")
FIRST_START  = Path("/first_start")
PORT         = 9142

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Speedtest execution ───────────────────────────────────────────────────────

def run_speedtest() -> dict | None:
    """
    Run the Ookla speedtest CLI and return parsed JSON results.
    Handles first-run license/GDPR acceptance automatically.
    Returns None on any failure.
    """
    first_run = not FIRST_START.exists()

    cmd = ["/bin/speedtest", "--format=json"]
    if first_run:
        cmd += ["--accept-license", "--accept-gdpr"]
        log.info("First run detected — accepting Ookla license and GDPR automatically.")
    if SERVER_ID:
        cmd.append(f"--server-id={SERVER_ID}")
        log.info("Using specified server ID: %s", SERVER_ID)
    else:
        log.info("No SERVER_ID specified — Ookla will auto-select the best server.")

    log.info("Starting speedtest...")
    start_time = time.time()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        elapsed = time.time() - start_time
        raw = result.stdout

        if first_run:
            # License preamble appears before the JSON object on first run.
            # Locate the JSON by finding the first '{' rather than relying on
            # a fixed line count (more robust across CLI versions).
            json_start = raw.find("{")
            if json_start == -1:
                log.error("No JSON found in speedtest output on first run.")
                log.error("stdout: %s", raw[:500])
                log.error("stderr: %s", result.stderr[:500])
                return None
            raw = raw[json_start:]
            FIRST_START.touch()
            log.info("First run complete. Ookla license and GDPR accepted.")

        data = json.loads(raw)

        log.info("Speedtest complete in %.1fs.", elapsed)
        log.info(
            "Server: %s (%s) | ISP: %s",
            data.get("server", {}).get("name", "unknown"),
            data.get("server", {}).get("location", "unknown"),
            data.get("isp", "unknown"),
        )
        log.info(
            "Results: Download=%.2f Mbps | Upload=%.2f Mbps | Ping=%.2f ms | Jitter=%.2f ms",
            (data["download"]["bandwidth"] * 8) / 1_000_000,
            (data["upload"]["bandwidth"] * 8) / 1_000_000,
            data["ping"]["latency"],
            data["ping"]["jitter"],
        )
        if "packetLoss" in data:
            log.info("Packet loss: %.2f%%", data["packetLoss"])

        return data

    except subprocess.TimeoutExpired:
        log.error("Speedtest timed out after 120 seconds.")
        return None
    except json.JSONDecodeError as exc:
        log.error("Failed to parse speedtest JSON: %s", exc)
        log.error("Raw output: %s", raw[:500] if "raw" in dir() else "unavailable")
        return None
    except Exception as exc:
        log.error("Unexpected error running speedtest: %s", exc)
        return None


def write_cache(data: dict) -> None:
    """
    Atomically write speedtest JSON to the cache file.
    Writes to a temp file first, then renames (rename is atomic on Linux).
    """
    CACHE_TMP.write_text(json.dumps(data))
    os.rename(CACHE_TMP, CACHE_PATH)
    log.info("Cache updated: %s", CACHE_PATH)


# ── Metrics parsing ───────────────────────────────────────────────────────────

def parse_metrics(data: dict) -> dict:
    """
    Extract metrics from speedtest JSON and convert units.
    Bandwidth is converted from bytes/s to Mbps (SI: * 8 / 1,000,000).
    Returns an empty dict on parse failure.
    """
    def to_mbps(bps: float) -> float:
        return round((bps * 8) / 1_000_000, 2)

    try:
        return {
            "download_mbps":        to_mbps(data["download"]["bandwidth"]),
            "upload_mbps":          to_mbps(data["upload"]["bandwidth"]),
            "ping_latency_ms":      data["ping"]["latency"],
            "ping_jitter_ms":       data["ping"]["jitter"],
            "download_latency_ms":  data["download"]["latency"]["iqm"],
            "upload_latency_ms":    data["upload"]["latency"]["iqm"],
            "packet_loss":          data.get("packetLoss"),  # None if not present
            "server_name":          data["server"]["name"],
            "server_location":      data["server"]["location"],
            "isp":                  data["isp"],
            "timestamp":            time.time(),
            "success":              1.0,
        }
    except (KeyError, TypeError) as exc:
        log.error("Failed to parse metrics from speedtest data: %s", exc)
        return {}


# ── Prometheus collector ──────────────────────────────────────────────────────

# Lock and shared result for on_demand mode — ensures only one speedtest runs
# at a time. Concurrent scrapes block until the in-progress test finishes,
# then all return the same result.
_speedtest_lock   = threading.Lock()
_last_result: dict | None = None


class SpeedtestCollector:
    """
    Custom Prometheus collector.
    on_demand mode: runs a live speedtest on each collect() call.
    cached mode:    reads from the cache file on each collect() call.
    """

    def collect(self):
        if SCRAPE_MODE == "cached":
            metrics = self._collect_cached()
        else:
            metrics = self._collect_on_demand()

        yield from self._build_metric_families(metrics)

    def _collect_on_demand(self) -> dict:
        global _last_result

        if _speedtest_lock.locked():
            log.info("Scrape requested while a speedtest is already in progress — waiting for it to finish...")

        with _speedtest_lock:
            log.info("Prometheus scrape received — running speedtest now...")
            data = run_speedtest()
            if data is None:
                log.error("Speedtest failed — returning scrape_success=0.")
                _last_result = {"success": 0.0, "timestamp": time.time()}
            else:
                _last_result = parse_metrics(data)
                if not _last_result:
                    log.error("Metric parsing failed — returning scrape_success=0.")
                    _last_result = {"success": 0.0, "timestamp": time.time()}
                else:
                    log.info("Metrics parsed successfully — serving to Prometheus.")
            return _last_result

    def _collect_cached(self) -> dict:
        if not CACHE_PATH.exists():
            log.warning("Cache file does not exist yet — no results available. Waiting for first cron run.")
            return {"success": 0.0, "timestamp": 0.0}

        try:
            with open(CACHE_PATH, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            log.info("Serving cached results to Prometheus.")
            return parse_metrics(data)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to read cache file: %s", exc)
            return {"success": 0.0, "timestamp": 0.0}

    @staticmethod
    def _build_metric_families(m: dict):
        success = m.get("success", 0.0)
        ts      = m.get("timestamp", 0.0)

        g = GaugeMetricFamily("speedtest_scrape_success",
                              "1 if the last speedtest run succeeded, 0 if it failed")
        g.add_metric([], success)
        yield g

        g = GaugeMetricFamily("speedtest_last_run_timestamp",
                              "Unix timestamp of the last speedtest run")
        g.add_metric([], ts)
        yield g

        if not success:
            return

        labels      = ["server_name", "server_location", "isp"]
        label_vals  = [m.get("server_name", "unknown"),
                       m.get("server_location", "unknown"),
                       m.get("isp", "unknown")]

        specs = [
            ("speedtest_download_bandwidth_mbps", "Download bandwidth in Mbps",          "download_mbps"),
            ("speedtest_upload_bandwidth_mbps",   "Upload bandwidth in Mbps",            "upload_mbps"),
            ("speedtest_ping_latency_ms",         "Ping latency in milliseconds",        "ping_latency_ms"),
            ("speedtest_ping_jitter_ms",          "Ping jitter in milliseconds",         "ping_jitter_ms"),
            ("speedtest_download_latency_ms",     "Download latency IQM in milliseconds","download_latency_ms"),
            ("speedtest_upload_latency_ms",       "Upload latency IQM in milliseconds",  "upload_latency_ms"),
        ]
        for name, help_text, key in specs:
            g = GaugeMetricFamily(name, help_text, labels=labels)
            g.add_metric(label_vals, m[key])
            yield g

        # packet_loss is only emitted when the server reports it
        if m.get("packet_loss") is not None:
            g = GaugeMetricFamily("speedtest_packet_loss", "Packet loss percentage",
                                  labels=labels)
            g.add_metric(label_vals, m["packet_loss"])
            yield g


# ── Entry points ──────────────────────────────────────────────────────────────

def run_once() -> None:
    """
    Run a single speedtest, update the cache, and exit.
    Used by cron in cached mode via: python3 exporter.py --run-once
    """
    log.info("run-once: triggered by cron — running speedtest and updating cache...")
    data = run_speedtest()
    if data is None:
        log.error("run-once: speedtest failed — cache not updated.")
        sys.exit(1)
    write_cache(data)
    log.info("run-once: cache updated successfully — exiting.")


def serve() -> None:
    """
    Start the Prometheus HTTP server and block indefinitely.
    This is the foreground process that keeps the container alive.
    """
    if SCRAPE_MODE not in ("on_demand", "cached"):
        log.warning("Unknown SCRAPE_MODE '%s', defaulting to on_demand.", SCRAPE_MODE)

    # Remove default collectors (GC, Process, Platform) for focused output
    for collector in list(REGISTRY._names_to_collectors.values()):
        if type(collector).__name__ in ("GCCollector", "PlatformCollector", "ProcessCollector"):
            try:
                REGISTRY.unregister(collector)
            except Exception:
                pass

    REGISTRY.register(SpeedtestCollector())

    def _shutdown(signum, frame):
        log.info("Received signal %d — shutting down.", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    start_http_server(PORT)
    log.info("Prometheus exporter listening on :%d (mode: %s)", PORT, SCRAPE_MODE)
    if SCRAPE_MODE == "on_demand":
        log.info("on_demand mode: each Prometheus scrape will trigger a live speedtest (~20-40s).")
    else:
        log.info("cached mode: serving results from cache. Cron job updates cache on schedule.")

    while True:
        time.sleep(10)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--run-once" in sys.argv:
        run_once()
    else:
        serve()

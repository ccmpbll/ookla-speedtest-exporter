#!/usr/bin/env python3
"""
ookla-speedtest-exporter
Prometheus metrics exporter for Ookla Speedtest CLI.

Each Prometheus scrape triggers a live speedtest and returns the results.
"""

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

SERVER_ID   = os.environ.get("SERVER_ID", "").strip()
FIRST_START = Path("/first_start")
PORT        = 9142

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
        log.error("Raw output: %s", raw[:500] if "raw" in locals() else "unavailable")
        return None
    except Exception as exc:
        log.error("Unexpected error running speedtest: %s", exc)
        return None


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
            # Ping
            "ping_latency_ms":              data["ping"]["latency"],
            "ping_jitter_ms":               data["ping"]["jitter"],
            "ping_low_ms":                  data["ping"]["low"],
            "ping_high_ms":                 data["ping"]["high"],
            # Download
            "download_mbps":                to_mbps(data["download"]["bandwidth"]),
            "download_bytes":               data["download"]["bytes"],
            "download_elapsed_ms":          data["download"]["elapsed"],
            "download_latency_low_ms":      data["download"]["latency"]["low"],
            "download_latency_high_ms":     data["download"]["latency"]["high"],
            "download_latency_jitter_ms":   data["download"]["latency"]["jitter"],
            # Upload
            "upload_mbps":                  to_mbps(data["upload"]["bandwidth"]),
            "upload_bytes":                 data["upload"]["bytes"],
            "upload_elapsed_ms":            data["upload"]["elapsed"],
            "upload_latency_low_ms":        data["upload"]["latency"]["low"],
            "upload_latency_high_ms":       data["upload"]["latency"]["high"],
            "upload_latency_jitter_ms":     data["upload"]["latency"]["jitter"],
            # Packet loss
            "packet_loss":                  data.get("packetLoss"),  # None if not present
            # Labels for measurement metrics
            "server_name":                  data["server"]["name"],
            "server_location":              data["server"]["location"],
            "isp":                          data["isp"],
            # Info metric fields
            "server_id":                    str(data["server"]["id"]),
            "server_country":               data["server"]["country"],
            "external_ip":                  data["interface"]["externalIp"],
            # Metadata
            "timestamp":                    time.time(),
            "success":                      1.0,
        }
    except (KeyError, TypeError) as exc:
        log.error("Failed to parse metrics from speedtest data: %s", exc)
        return {}


# ── Prometheus collector ──────────────────────────────────────────────────────

# Lock and shared result — ensures only one speedtest runs at a time.
# Concurrent scrapes block until the in-progress test finishes,
# then all return the same result without triggering another run.
_speedtest_lock    = threading.Lock()
_speedtest_running = False   # True while a test is actively in progress
_last_result: dict | None = None


class SpeedtestCollector:
    """
    Custom Prometheus collector.
    Each scrape triggers a live speedtest. Concurrent scrapes block and
    share the result of the in-progress test.
    """

    def collect(self):
        yield from self._build_metric_families(self._collect())

    def _collect(self) -> dict:
        global _last_result, _speedtest_running

        was_waiting = _speedtest_running
        if was_waiting:
            log.info("Scrape requested while a speedtest is already in progress — waiting for it to finish...")

        with _speedtest_lock:
            # If we were waiting and a result is now available, return it
            # directly rather than kicking off another speedtest.
            if was_waiting and _last_result is not None:
                log.info("Returning result from completed speedtest to queued scrape.")
                return _last_result

            _speedtest_running = True
            log.info("Prometheus scrape received — running speedtest now...")
            try:
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
            finally:
                _speedtest_running = False
            return _last_result

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

        labels     = ["server_name", "server_location", "isp"]
        label_vals = [m.get("server_name", "unknown"),
                      m.get("server_location", "unknown"),
                      m.get("isp", "unknown")]

        # ── Numeric metrics ───────────────────────────────────────────────────
        specs = [
            # Ping
            ("speedtest_ping_latency_ms",              "Ping latency in milliseconds",                  "ping_latency_ms"),
            ("speedtest_ping_jitter_ms",               "Ping jitter in milliseconds",                   "ping_jitter_ms"),
            ("speedtest_ping_low_ms",                  "Ping low in milliseconds",                      "ping_low_ms"),
            ("speedtest_ping_high_ms",                 "Ping high in milliseconds",                     "ping_high_ms"),
            # Download
            ("speedtest_download_bandwidth_mbps",      "Download bandwidth in Mbps",                    "download_mbps"),
            ("speedtest_download_bytes",               "Total bytes received during download test",      "download_bytes"),
            ("speedtest_download_elapsed_ms",          "Download test duration in milliseconds",         "download_elapsed_ms"),
            ("speedtest_download_latency_low_ms",      "Download latency low in milliseconds",          "download_latency_low_ms"),
            ("speedtest_download_latency_high_ms",     "Download latency high in milliseconds",         "download_latency_high_ms"),
            ("speedtest_download_latency_jitter_ms",   "Download latency jitter in milliseconds",       "download_latency_jitter_ms"),
            # Upload
            ("speedtest_upload_bandwidth_mbps",        "Upload bandwidth in Mbps",                      "upload_mbps"),
            ("speedtest_upload_bytes",                 "Total bytes sent during upload test",            "upload_bytes"),
            ("speedtest_upload_elapsed_ms",            "Upload test duration in milliseconds",           "upload_elapsed_ms"),
            ("speedtest_upload_latency_low_ms",        "Upload latency low in milliseconds",            "upload_latency_low_ms"),
            ("speedtest_upload_latency_high_ms",       "Upload latency high in milliseconds",           "upload_latency_high_ms"),
            ("speedtest_upload_latency_jitter_ms",     "Upload latency jitter in milliseconds",         "upload_latency_jitter_ms"),
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

        # ── Info metric ───────────────────────────────────────────────────────
        # Carries string-valued fields not suitable as numeric metrics.
        info_labels     = ["server_id", "server_country", "external_ip"]
        info_label_vals = [m.get("server_id", "unknown"),
                           m.get("server_country", "unknown"),
                           m.get("external_ip", "unknown")]
        g = GaugeMetricFamily("speedtest_info",
                              "Speedtest result metadata (server_id, server_country, external_ip)",
                              labels=info_labels)
        g.add_metric(info_label_vals, 1.0)
        yield g


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
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
    log.info("Prometheus exporter listening on :%d", PORT)
    log.info("Each Prometheus scrape will trigger a live speedtest (~20-40s).")

    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()

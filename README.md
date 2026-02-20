# ookla-speedtest-exporter
![Image Build Status](https://img.shields.io/github/actions/workflow/status/ccmpbll/ookla-speedtest-exporter/docker-image.yml?branch=main) ![Docker Image Size](https://img.shields.io/docker/image-size/ccmpbll/ookla-speedtest-exporter/latest) ![Docker Pulls](https://img.shields.io/docker/pulls/ccmpbll/ookla-speedtest-exporter.svg) ![License](https://img.shields.io/badge/License-GPLv3-blue.svg)

A Prometheus exporter that runs [Ookla's Speedtest CLI](https://www.speedtest.net/apps/cli) and exposes the results as Prometheus metrics on port `9142`.

Supports two scrape modes:
- **`on_demand`** (default) — runs a live speedtest on each Prometheus scrape
- **`cached`** — runs speedtest on a cron schedule and serves cached results instantly

---

## Scrape Modes

### `on_demand` (default)
Prometheus scrapes `/metrics` → speedtest runs live → results returned immediately.

Simple to set up, no cron needed. Because a speedtest takes 20–40 seconds, you **must** set `scrape_timeout` in your Prometheus config (see example below). The exporter ensures only one speedtest runs at a time — concurrent scrapes block and share the result.

### `cached`
A cron job runs the speedtest in the background on a schedule you define. Prometheus scrapes are served instantly from the cached result.

Use this mode when you want fast scrape responses or need to decouple the test schedule from Prometheus. Requires the `CRON` environment variable.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SCRAPE_MODE` | No | `on_demand` | `on_demand` or `cached` |
| `TZ` | No | system default | Timezone in tz database format, e.g. `America/New_York` |
| `SERVER_ID` | No | auto-select | Ookla server numeric ID to force a specific test server |
| `CRON` | cached mode only | `0 * * * *` | Cron schedule expression for background test runs |

---

## Quick Start

### `on_demand` mode
```bash
docker run -d \
  --name ookla-speedtest-exporter \
  -e SCRAPE_MODE=on_demand \
  -e TZ=America/New_York \
  -p 9142:9142 \
  ccmpbll/ookla-speedtest-exporter:latest
```

### `cached` mode (hourly)
```bash
docker run -d \
  --name ookla-speedtest-exporter \
  -e SCRAPE_MODE=cached \
  -e CRON='0 * * * *' \
  -e TZ=America/New_York \
  -p 9142:9142 \
  ccmpbll/ookla-speedtest-exporter:latest
```

### Docker Compose
```yaml
services:
  speedtest-exporter:
    image: ccmpbll/ookla-speedtest-exporter:latest
    environment:
      - SCRAPE_MODE=on_demand
      - TZ=America/New_York
    ports:
      - "9142:9142"
    restart: unless-stopped
```

---

## Prometheus Configuration

```yaml
scrape_configs:
  - job_name: 'speedtest'
    static_configs:
      - targets: ['speedtest-exporter:9142']
    # Required for on_demand mode — speedtest takes 20-40 seconds
    scrape_timeout: 60s
    # Set scrape_interval to control how often tests run in on_demand mode
    scrape_interval: 5m
```

> **Note:** In `cached` mode the scrape completes instantly, so the default `scrape_timeout` of 10s is fine. The `CRON` expression controls test frequency instead.

---

## Exposed Metrics

All measurement metrics include the labels `server_name`, `server_location`, and `isp`.

| Metric | Type | Description |
|---|---|---|
| `speedtest_download_bandwidth_mbps` | Gauge | Download speed in Mbps |
| `speedtest_upload_bandwidth_mbps` | Gauge | Upload speed in Mbps |
| `speedtest_ping_latency_ms` | Gauge | Ping latency in milliseconds |
| `speedtest_ping_jitter_ms` | Gauge | Ping jitter in milliseconds |
| `speedtest_download_latency_ms` | Gauge | Download latency IQM in milliseconds |
| `speedtest_upload_latency_ms` | Gauge | Upload latency IQM in milliseconds |
| `speedtest_packet_loss` | Gauge | Packet loss percentage (only emitted when reported by the test server) |
| `speedtest_last_run_timestamp` | Gauge | Unix timestamp of the last speedtest run |
| `speedtest_scrape_success` | Gauge | `1` if the last run succeeded, `0` if it failed |

### Example output
```
# HELP speedtest_scrape_success 1 if the last speedtest run succeeded, 0 if it failed
# TYPE speedtest_scrape_success gauge
speedtest_scrape_success 1.0
# HELP speedtest_last_run_timestamp Unix timestamp of the last speedtest run
# TYPE speedtest_last_run_timestamp gauge
speedtest_last_run_timestamp 1.708123456e+09
# HELP speedtest_download_bandwidth_mbps Download bandwidth in Mbps
# TYPE speedtest_download_bandwidth_mbps gauge
speedtest_download_bandwidth_mbps{isp="Comcast",server_location="Chicago, IL",server_name="Speedtest Chicago"} 452.34
# HELP speedtest_upload_bandwidth_mbps Upload bandwidth in Mbps
# TYPE speedtest_upload_bandwidth_mbps gauge
speedtest_upload_bandwidth_mbps{isp="Comcast",server_location="Chicago, IL",server_name="Speedtest Chicago"} 23.11
```

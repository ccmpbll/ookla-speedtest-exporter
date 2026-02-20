# ookla-speedtest-exporter
![Image Build Status](https://img.shields.io/github/actions/workflow/status/ccmpbll/ookla-speedtest-exporter/docker-image.yml?branch=main) ![Docker Image Size](https://img.shields.io/docker/image-size/ccmpbll/ookla-speedtest-exporter/latest) ![Docker Pulls](https://img.shields.io/docker/pulls/ccmpbll/ookla-speedtest-exporter.svg) ![License](https://img.shields.io/badge/License-GPLv3-blue.svg)

A Prometheus exporter that runs [Ookla's Speedtest CLI](https://www.speedtest.net/apps/cli) and exposes the results as Prometheus metrics on port `9142`.

Each Prometheus scrape triggers a live speedtest (~20-40 seconds). Because of this, you **must** set `scrape_timeout` in your Prometheus config (see example below). The exporter ensures only one speedtest runs at a time — concurrent scrapes block and share the result.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TZ` | No | system default | Timezone in tz database format, e.g. `America/New_York` |
| `SERVER_ID` | No | auto-select | Ookla server numeric ID to force a specific test server |

---

## Quick Start

```bash
docker run -d \
  --name ookla-speedtest-exporter \
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
    # Required — speedtest takes 20-40 seconds
    scrape_timeout: 60s
    # Controls how often tests run
    scrape_interval: 1h
```

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

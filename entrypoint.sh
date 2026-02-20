#!/bin/bash

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" > /proc/1/fd/1; }

log "Starting ookla-speedtest-exporter..."
log "Starting Prometheus exporter on port 9142..."
exec python3 /usr/bin/exporter.py

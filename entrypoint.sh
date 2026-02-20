#!/bin/bash

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" > /proc/1/fd/1; }

cron_regex_test() {
    local CRONEXP="$1"
    local REGEX='^((((\d+,)+\d+|(\d+(\/|-|#)\d+)|\d+L?|\*(\/\d+)?|L(-\d+)?|\?|[A-Z]{3}(-[A-Z]{3})?) ?){5,7})|(@(annually|yearly|monthly|weekly|daily|hourly|reboot))|(@every (\d+(s|m|h))+)$'
    if echo "$CRONEXP" | grep -Pq "$REGEX"; then
        return 0
    else
        return 1
    fi
}

log "Starting ookla-speedtest-exporter in ${SCRAPE_MODE:-on_demand} mode..."

if [[ "${SCRAPE_MODE}" == "cached" ]]; then
    log "Cached mode: configuring cron job for background speedtest runs..."

    if [[ -n "$CRON" ]]; then
        log "Cron expression was specified, testing..."
        if cron_regex_test "$CRON"; then
            log "Cron expression is valid: $CRON"
            log "Setting up cron job..."
            echo "$CRON python3 /usr/bin/exporter.py --run-once >> /proc/1/fd/1 2>&1" | crontab -
        else
            log "Cron expression was invalid: $CRON, defaulting to hourly..."
            log "Setting up cron job..."
            echo "0 * * * * python3 /usr/bin/exporter.py --run-once >> /proc/1/fd/1 2>&1" | crontab -
        fi
    else
        log "CRON not specified, defaulting to hourly..."
        log "Setting up cron job..."
        echo "0 * * * * python3 /usr/bin/exporter.py --run-once >> /proc/1/fd/1 2>&1" | crontab -
    fi

    # Export env vars so cron-spawned processes can see SERVER_ID, TZ, etc.
    printenv > /etc/environment

    log "Starting cron daemon..."
    cron
else
    log "on_demand mode: speedtest will run live on each Prometheus scrape."
fi

log "Starting Prometheus exporter on port 9142..."
exec python3 /usr/bin/exporter.py

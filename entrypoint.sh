#!/bin/bash

LOG_DATE_FORMAT="$(date +%D_%T)"

cron_regex_test() {
    local CRONEXP="$1"
    local REGEX='^((((\d+,)+\d+|(\d+(\/|-|#)\d+)|\d+L?|\*(\/\d+)?|L(-\d+)?|\?|[A-Z]{3}(-[A-Z]{3})?) ?){5,7})|(@(annually|yearly|monthly|weekly|daily|hourly|reboot))|(@every (\d+(s|m|h))+)$'
    if echo "$CRONEXP" | grep -Pq "$REGEX"; then
        return 0
    else
        return 1
    fi
}

echo "$LOG_DATE_FORMAT - Starting ookla-speedtest-exporter in ${SCRAPE_MODE:-on_demand} mode..." > /proc/1/fd/1

if [[ "${SCRAPE_MODE}" == "cached" ]]; then
    echo "$LOG_DATE_FORMAT - Cached mode: configuring cron job for background speedtest runs..." > /proc/1/fd/1

    if [[ -n "$CRON" ]]; then
        echo "$LOG_DATE_FORMAT - Cron expression was specified, testing..." > /proc/1/fd/1
        if cron_regex_test "$CRON"; then
            echo "$LOG_DATE_FORMAT - Cron expression is valid: $CRON" > /proc/1/fd/1
            echo "$LOG_DATE_FORMAT - Setting up cron job..." > /proc/1/fd/1
            echo "$CRON python3 /usr/bin/exporter.py --run-once >> /proc/1/fd/1 2>&1" | crontab -
        else
            echo "$LOG_DATE_FORMAT - Cron expression was invalid: $CRON, defaulting to hourly..." > /proc/1/fd/1
            echo "$LOG_DATE_FORMAT - Setting up cron job..." > /proc/1/fd/1
            echo "0 * * * * python3 /usr/bin/exporter.py --run-once >> /proc/1/fd/1 2>&1" | crontab -
        fi
    else
        echo "$LOG_DATE_FORMAT - CRON not specified, defaulting to hourly..." > /proc/1/fd/1
        echo "$LOG_DATE_FORMAT - Setting up cron job..." > /proc/1/fd/1
        echo "0 * * * * python3 /usr/bin/exporter.py --run-once >> /proc/1/fd/1 2>&1" | crontab -
    fi

    # Export env vars so cron-spawned processes can see SERVER_ID, TZ, etc.
    printenv > /etc/environment

    echo "$LOG_DATE_FORMAT - Starting cron daemon..." > /proc/1/fd/1
    cron
else
    echo "$LOG_DATE_FORMAT - on_demand mode: speedtest will run live on each Prometheus scrape." > /proc/1/fd/1
fi

echo "$LOG_DATE_FORMAT - Starting Prometheus exporter on port 9142..." > /proc/1/fd/1
exec python3 /usr/bin/exporter.py

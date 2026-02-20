FROM debian:trixie-slim
LABEL Name=ookla-speedtest-exporter
LABEL maintainer="Chris Campbell"

ARG SPEEDTEST_CLI_VERSION="1.2.0"
ENV TZ=
ENV SCRAPE_MODE=on_demand
ENV CRON=
ENV SERVER_ID=

RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends tzdata bash cron curl python3 python3-pip \
 && pip3 install --no-cache-dir prometheus_client --break-system-packages \
 && apt-get purge -y python3-pip \
 && apt-get autoremove -y \
 && curl -fsSL "https://install.speedtest.net/app/cli/ookla-speedtest-${SPEEDTEST_CLI_VERSION}-linux-x86_64.tgz" \
      -o /tmp/ookla-speedtest.tgz \
 && tar zxf /tmp/ookla-speedtest.tgz -C /tmp speedtest \
 && mv /tmp/speedtest /bin/speedtest \
 && chmod +x /bin/speedtest \
 && rm /tmp/ookla-speedtest.tgz \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY --chmod=755 exporter.py /usr/bin/exporter.py
COPY --chmod=755 entrypoint.sh /usr/bin/entrypoint.sh

EXPOSE 9142

ENTRYPOINT ["entrypoint.sh"]

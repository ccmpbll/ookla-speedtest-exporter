FROM debian:bookworm-slim
LABEL Name=ookla-speedtest-exporter
LABEL maintainer="Chris Campbell"

ARG SPEEDTEST_CLI_VERSION="1.2.0"
ENV TZ=
ENV SCRAPE_MODE=on_demand
ENV CRON=
ENV SERVER_ID=

RUN apt update && apt full-upgrade -y
RUN apt install -y tzdata bash cron curl wget nano python3 python3-pip
RUN apt clean && apt autoremove -y

RUN pip3 install prometheus_client --break-system-packages

RUN wget https://install.speedtest.net/app/cli/ookla-speedtest-${SPEEDTEST_CLI_VERSION}-linux-x86_64.tgz -O /tmp/ookla-speedtest.tgz
RUN tar zxvf /tmp/ookla-speedtest.tgz -C /tmp speedtest
RUN mv /tmp/speedtest /bin/speedtest
RUN chmod +x /bin/speedtest
RUN rm /tmp/ookla-speedtest.tgz

COPY exporter.py /usr/bin/exporter.py
RUN chmod +x /usr/bin/exporter.py

COPY entrypoint.sh /usr/bin/entrypoint.sh
RUN chmod +x /usr/bin/entrypoint.sh

EXPOSE 9142

ENTRYPOINT ["entrypoint.sh"]

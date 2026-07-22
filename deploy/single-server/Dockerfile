# Single-server test image: official Nautobot + nautobot_day2 installed on top.
ARG NAUTOBOT_IMAGE=ghcr.io/nautobot/nautobot:2.3-py3.11
FROM ${NAUTOBOT_IMAGE}

COPY --chown=nautobot:nautobot . /source/nautobot-day2
RUN pip install --no-cache-dir "/source/nautobot-day2[broker]"

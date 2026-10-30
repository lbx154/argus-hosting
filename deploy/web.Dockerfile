FROM python:3.12-slim-trixie

ARG ARGUS_SKILL_BUILD_REVISION=""
ENV ARGUS_SKILL_BUILD_REVISION=${ARGUS_SKILL_BUILD_REVISION}

ARG TRIAL_UID=1000
ARG TRIAL_GID=100
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ripgrep socat tini \
    && apt-get clean \
    && useradd --no-log-init --uid "$TRIAL_UID" --gid "$TRIAL_GID" \
        --home-dir /tenant/home trial

# Build with --build-context argus-source=/path/to/Argus.
WORKDIR /opt/argus
COPY --from=argus-source / /opt/argus
RUN pip install --no-cache-dir /opt/argus
WORKDIR /opt/argus-hosting
COPY pyproject.toml README.md LICENSE ./
COPY argus_hosting ./argus_hosting
COPY argus_trial_server ./argus_trial_server
RUN pip install --no-cache-dir -e /opt/argus-hosting
COPY --from=runtime-tools /copilot /usr/local/bin/copilot

ENV HOME=/tenant/home \
    ARGUS_SKILL_HOME=/tenant/home/.argus-skill \
    ARGUS_SKILL_COPILOT_TRIAL=1 \
    ARGUS_SKILL_RUNNER_BACKEND=copilot \
    ARGUS_SKILL_LIFE_BACKEND=copilot \
    ARGUS_SKILL_RUNNER_BIN=/usr/local/bin/copilot \
    ARGUS_SKILL_MODEL=gpt-5.5 \
    ARGUS_SKILL_BACKEND_AUTH_MODE=subscription_cli \
    PYTHONPATH=/opt/argus \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER trial
WORKDIR /tenant/workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "argus_trial_server.web_runtime"]

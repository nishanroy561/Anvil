# Anvil on a Hugging Face Docker Space — API + worker in one container.
#
# Spaces give you a single container, so the two processes that compose runs
# separately share one here (see start.sh for why that is not as bad as it
# sounds, and for the one rule that makes it safe).
#
# The source is cloned at build time rather than copied, so this file can live in
# a small Space repo of its own and Anvil's own repo stays untouched — no root
# Dockerfile, no YAML frontmatter bolted onto the project README. Rebuild the
# Space to pick up new commits; pin ANVIL_REF to a tag if you want it to stop
# moving.

FROM python:3.12-slim

# Spaces run the container as uid 1000. Creating that user here means the pip
# --user install below lands somewhere the runtime can actually read.
RUN useradd -m -u 1000 user \
 && apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

ARG ANVIL_REPO=https://github.com/nishanroy561/Anvil
ARG ANVIL_REF=main
RUN git clone --depth 1 --branch ${ANVIL_REF} ${ANVIL_REPO} anvil

# Both services, no extras. `[judges]` is deliberately omitted: a public demo
# where any visitor can start an eval run must not be able to spend your provider
# credits. Without the SDKs *and* without keys, judges fail with an item-level
# "no judge key configured" and the run still completes — the six deterministic
# graders demonstrate the eval loop perfectly well.
RUN pip install --no-cache-dir --user -e anvil/apps/api -e anvil/apps/worker

COPY --chown=user start.sh /home/user/start.sh
RUN chmod +x /home/user/start.sh

# Spaces route to 7860 unless the README's `app_port` says otherwise.
ENV PORT=7860
EXPOSE 7860

CMD ["/home/user/start.sh"]

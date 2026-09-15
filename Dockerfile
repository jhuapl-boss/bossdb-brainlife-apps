# Builds Cloudome and makes the single-script local runner the container command.
# Example:
#   docker build -t local-contactome .
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/results:/work/results" \
#     -e MIP=64,64,40 -e ENQUEUE_LIMIT=10 local-contactome \
#     precomputed://s3://bossdb-open-data/iarpa_microns/pinky/seg /work/results
# Override at build time to test another Cloudome revision without editing this file.
ARG CLOUDOME_REPOSITORY=https://github.com/aplbrain/cloudome.git
ARG CLOUDOME_REF=6854220de261ce6ddf945068d7a1a2af7eb2e635

FROM alpine/git:latest AS cloudome-source
ARG CLOUDOME_REPOSITORY
ARG CLOUDOME_REF
RUN git clone "$CLOUDOME_REPOSITORY" /cloudome \
 && git -C /cloudome checkout --detach "$CLOUDOME_REF" \
 && rm -rf /cloudome/.git

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
WORKDIR /opt/contactome
COPY --from=cloudome-source /cloudome ./cloudome
RUN uv sync --directory cloudome --frozen --no-dev
ENV HOME=/tmp \
    UV_CACHE_DIR=/tmp/uv-cache

COPY --chmod=755 run_local_contactome.sh ./
RUN ./run_local_contactome.sh --help >/dev/null

WORKDIR /work
ENTRYPOINT ["/opt/contactome/run_local_contactome.sh"]

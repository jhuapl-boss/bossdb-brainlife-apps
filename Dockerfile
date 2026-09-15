FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

FROM base AS model-assets
ARG MODEL_S3_URI="s3://bossdb-neuvue-datalake/public/models/20260825_191045 trial 1"
COPY download_model.py /opt/bossdb-nuclei/download_model.py
RUN python /opt/bossdb-nuclei/download_model.py "$MODEL_S3_URI" \
    /opt/bossdb-nuclei/models/20260825_191045_monai_basic_unet3d

FROM base AS runtime
WORKDIR /opt/bossdb-nuclei
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt /opt/bossdb-nuclei/requirements.lock.txt
COPY pytorch_connectomics /opt/bossdb-nuclei/pytorch_connectomics
RUN pip install -r /opt/bossdb-nuclei/requirements.lock.txt
COPY nuclei_inference.py /opt/bossdb-nuclei/nuclei_inference.py

FROM runtime AS final
COPY --from=model-assets /opt/bossdb-nuclei/models /opt/bossdb-nuclei/models
WORKDIR /work
ENTRYPOINT ["python", "/opt/bossdb-nuclei/nuclei_inference.py"]

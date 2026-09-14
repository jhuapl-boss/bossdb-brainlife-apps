FROM python:3.12-slim

COPY requirements.lock.txt /opt/bossdb-tiff/requirements.lock.txt
RUN pip install --no-cache-dir -r /opt/bossdb-tiff/requirements.lock.txt

COPY export_tiff.py /opt/bossdb-tiff/export_tiff.py

WORKDIR /work
ENTRYPOINT ["python", "/opt/bossdb-tiff/export_tiff.py"]

FROM python:3.11-slim
COPY requirements.lock.txt /opt/reference-inspect/requirements.lock.txt
RUN pip install --no-cache-dir -r /opt/reference-inspect/requirements.lock.txt
COPY inspect_reference.py /opt/reference-inspect/inspect_reference.py
WORKDIR /work
ENTRYPOINT ["python", "/opt/reference-inspect/inspect_reference.py"]

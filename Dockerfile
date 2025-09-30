FROM python:3.12
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
WORKDIR /app
COPY main.py /app/

# Local Test 
COPY data/ /app/data/
COPY config.json.example /app/config.json


#RUN chmod +x /app/run.py
#CMD ["./run.sh"]
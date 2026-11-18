# Live-feed consumer. Build context is ./worker, which contains the matcher
# (match.py) and storage (store.py) it imports.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "consume_live.py"]

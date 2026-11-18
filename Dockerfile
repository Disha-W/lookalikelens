# LookalikeLens - all-in-one dev image (web app + worker tools + tests).
# Python 3.12: requirements.txt pins need >=3.10.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

# Faster, cleaner Python in a container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# data/ is a mounted volume at runtime (forecast.csv + forecast.db live there).
CMD ["python", "scheduler.py"]

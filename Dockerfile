FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    OPS_DB_PATH=/data/ops.sqlite3

WORKDIR /app
COPY requirements-push.txt ./
RUN pip install --no-cache-dir -r requirements-push.txt
RUN useradd --system --uid 10001 --create-home ops && mkdir -p /data && chown ops:ops /data
COPY --chown=ops:ops server.py operations.py closing_reports.py staffing.py compensation.py notification_center.py push_delivery.py schema.sql reset_operational_data.py ./
COPY --chown=ops:ops static ./static
COPY --chown=ops:ops templates ./templates
USER ops
EXPOSE 8000
CMD ["python3", "server.py"]

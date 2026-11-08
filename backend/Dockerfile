FROM python:3.11-slim
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && mkdir /data && chown 10001:10001 /data
COPY backend /app/backend
COPY worker/__init__.py worker/interpreter.py /app/worker/
COPY schemas /app/schemas
COPY examples /app/examples
COPY docs/03_BPMN_API_CONTRACT.md /app/docs/03_BPMN_API_CONTRACT.md
USER 10001:10001
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

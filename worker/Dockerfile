FROM python:3.11-slim
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/app/domain.py backend/app/builder.py backend/app/graph.py backend/app/document_builder.py backend/app/bpmn_schema.py /app/backend/app/
COPY schemas /app/schemas
COPY backend/__init__.py /app/backend/__init__.py
COPY backend/app/__init__.py /app/backend/app/__init__.py
COPY worker /app/worker
USER 10001:10001
CMD ["python", "-m", "worker.server"]

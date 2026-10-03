FROM python:3.13-slim
WORKDIR /opt/ops-pilot
COPY pyproject.toml README.md constraints-demo.txt ./
COPY app ./app
COPY knowledge ./knowledge
COPY scripts/create_demo_accounts.py ./scripts/create_demo_accounts.py
RUN pip install --no-cache-dir -c constraints-demo.txt . && useradd --uid 10001 --create-home demo \
    && mkdir -p data && chown demo:demo data
USER demo
ENV AGENT_MODE=langgraph KNOWLEDGE_RETRIEVER=candidate DATABASE_PATH=data/ops_pilot.db PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

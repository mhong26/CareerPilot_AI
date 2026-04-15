FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

# Copy dependency spec and minimal package stub for editable install
COPY pyproject.toml .
COPY app/ app/

RUN uv pip install --system -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

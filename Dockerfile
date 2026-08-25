FROM python:3.12-slim

WORKDIR /app

COPY requirements-node.txt .
RUN pip install --no-cache-dir -r requirements-node.txt

COPY . .

ENV BTPY_DATA_DIR=/data \
    BTPY_P2P_HOST=0.0.0.0 \
    BTPY_P2P_PORT=26000 \
    BTPY_ENABLE_P2P=1

EXPOSE 8000 26000

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --log-level warning"]
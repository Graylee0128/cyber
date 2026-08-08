# build context = repo root（compose 裡設定），才能拿到 src/ 與 pyproject.toml
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY config /app/config
RUN pip install --no-cache-dir -e .
EXPOSE 8000
CMD ["python", "-m", "purple.receiver.server"]

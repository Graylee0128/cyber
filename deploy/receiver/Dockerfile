# build context = repo root（compose 裡設定），才能拿到 src/ 與 pyproject.toml
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY config /app/config
# --timeout / --retries：2026-08-10 實測在 ~190 kB/s 的連線下，psycopg-binary(5.2MB)
# 會在讀取途中 ReadTimeout 而讓整個 deploy.sh 中止。pip 預設 15s timeout 對慢速鏡像
# 太短，且預設的 retry 不涵蓋「讀到一半逾時」。放寬後一鍵部署才禁得起網路抖動。
RUN pip install --no-cache-dir --timeout 120 --retries 10 -e .
EXPOSE 8000
CMD ["python", "-m", "purple.receiver.server"]

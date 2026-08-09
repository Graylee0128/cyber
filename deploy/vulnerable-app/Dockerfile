FROM python:3.12-slim
WORKDIR /app
# OTLP push（票 #11）：以 gRPC exporter 把 ssh_failed_logins 推到 :4317。
RUN pip install --no-cache-dir \
      opentelemetry-sdk==1.27.0 \
      opentelemetry-exporter-otlp-proto-grpc==1.27.0
COPY deploy/vulnerable-app/app.py /app/app.py
EXPOSE 8080
CMD ["python", "-u", "app.py"]

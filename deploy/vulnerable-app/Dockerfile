FROM python:3.12-slim
WORKDIR /app
COPY deploy/vulnerable-app/app.py /app/app.py
EXPOSE 8080
CMD ["python", "-u", "app.py"]

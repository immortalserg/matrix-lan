FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY *.py /app/
USER 10001:10001
CMD ["python", "/app/app.py"]

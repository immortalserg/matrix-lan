FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /monitor
COPY *.py /monitor/
USER 10001:10001
CMD ["python", "/monitor/monitor.py"]

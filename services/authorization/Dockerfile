FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir psycopg2-binary==2.9.10
COPY api.py registration.py bridge_accounts.py roles.py /app/
USER 10001:10001
EXPOSE 8081
CMD ["python", "/app/api.py"]

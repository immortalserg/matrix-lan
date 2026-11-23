FROM python:3.12-slim
RUN pip install --no-cache-dir aiohttp==3.13.2
ENV PYTHONUNBUFFERED=1
WORKDIR /wechat
COPY *.py /wechat/
USER 10001:10001
ENTRYPOINT ["python", "/wechat/app.py"]

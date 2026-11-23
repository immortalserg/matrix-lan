FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.56.0 aiohttp==3.13.2 Pillow==11.3.0
ENV PYTHONUNBUFFERED=1
WORKDIR /social
COPY *.py *.js /social/
USER 10001:10001
CMD ["python", "/social/app.py"]

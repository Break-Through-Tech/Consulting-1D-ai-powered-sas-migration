FROM python:3.11-slim
RUN pip install --no-cache-dir numpy==2.4.2 pandas==3.0.2 httpx==0.28.1
USER 65534:65534

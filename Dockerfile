FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data LLM_PROVIDER=cloud HOST=0.0.0.0
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --no-create-home -u 10001 bot
COPY . /app/
EXPOSE 8000
# The mounted Railway volume is root-owned initially. Entry point only adjusts
# /data ownership, then setuid/setgid(10001) before importing the application.
ENTRYPOINT ["python", "docker_entrypoint.py"]

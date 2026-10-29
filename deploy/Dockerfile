# 60-Second Fire Check — public demo image.
#
#   docker build -f deploy/Dockerfile -t firecheck:latest .
#   docker run -d --name firecheck --network coolify \
#     -v /srv/firecheck/runs:/app/runs \
#     -e META_MODEL_API_KEY -e FIRECHECK_PUBLIC=1 -e UPLOAD_PASSCODE \
#     -e FIRECHECK_SHOWCASE=run-a,run-b firecheck:latest
#
# Runs as an unprivileged user; the only writable path is /app/runs (a volume).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg/ffprobe do the preprocessing probe + encode and cut the SAM windows;
# tzdata keeps "today" in Asia/Seoul inside the slim image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# app/server.py imports pipeline/run_all.py, which imports gate/sam_video_gate.py and
# reads rulesets/kr-fire.json — all three directories have to be in the image.
COPY pipeline/ /app/pipeline/
COPY gate/ /app/gate/
COPY rulesets/ /app/rulesets/
COPY app/ /app/app/

RUN useradd --create-home --uid 10001 firecheck \
    && mkdir -p /app/runs \
    && chown -R firecheck:firecheck /app

USER firecheck

EXPOSE 8000
VOLUME ["/app/runs"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "app/server.py", "--host", "0.0.0.0", "--port", "8000"]
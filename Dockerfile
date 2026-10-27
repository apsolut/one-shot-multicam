# Multicam web app: docker build -t multicam . && docker run --rm -p 8787:8787 -v multicam-data:/data multicam
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir -r requirements.txt
COPY multicam ./multicam
COPY workflows ./workflows
COPY docs ./docs
RUN pip install --no-cache-dir -e . \
 && HF_HOME=/app/.hf python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
ENV MULTICAM_WORKFLOWS=/app/workflows PYTHONUNBUFFERED=1 MULTICAM_DATA_DIR=/data PORT=8787 HF_HOME=/app/.hf
RUN mkdir -p /data && chmod 777 /data && chmod -R a+rX /app   # runs as any uid (e.g. HF Spaces uid 1000)
VOLUME /data
EXPOSE 8787
# The Comfy key is NOT baked in: users paste it in the UI (kept in their browser only).
# Public hosted demo: add env MULTICAM_PUBLIC=1, MULTICAM_HOSTED_KEY_ENV=COMFY_API_KEY, MULTICAM_MAX_JOBS=15,
# MULTICAM_PROXY_HOPS=1 and the COMFY_API_KEY secret (all flags read MULTICAM_* env, PORT sets the port).
CMD ["multicam", "serve", "--host", "0.0.0.0"]

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 FORMAT_HUB_DATA=/data HOME=/tmp
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw fonts-noto-cjk libcairo2 libseccomp2 gcc libc6-dev ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY runtime/office-ipc.c ./runtime/office-ipc.c
RUN cc -shared -fPIC -O2 runtime/office-ipc.c -ldl -o runtime/liboffice-ipc.so
COPY run.py ./
RUN useradd --uid 10001 --create-home converter && mkdir /data && chown converter:converter /data
USER converter
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3)"
CMD ["python", "run.py"]

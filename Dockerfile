FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PROPDASH_DATA=/home/user/app/data PROPDASH_REQUIRE_PASSWORD=1 PROPDASH_LEAN=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt
ENV PATH=/home/user/.local/bin:$PATH TESSDATA_PREFIX=/home/user/app/tessdata
RUN mkdir -p tessdata data/models && python -c "import urllib.request as u; u.urlretrieve('https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata','tessdata/eng.traineddata'); u.urlretrieve('https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx','data/models/clip_vision_q.onnx')"
COPY --chown=user app ./app
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --log-level warning"]

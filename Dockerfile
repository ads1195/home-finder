FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PROPDASH_DATA=/home/user/app/data PROPDASH_REQUIRE_PASSWORD=1 PROPDASH_LEAN=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt
ENV PATH=/home/user/.local/bin:$PATH
COPY --chown=user app ./app
RUN mkdir -p data
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --log-level warning"]

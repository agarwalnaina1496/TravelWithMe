FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade \
        pip==26.1.2 \
        setuptools==83.0.0 \
        wheel==0.47.0 \
        jaraco.context==6.1.2 \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY twm ./twm

RUN addgroup --system twm && adduser --system --ingroup twm --no-create-home twm \
    && chown -R twm:twm /app

USER twm

EXPOSE 8000

CMD ["sh", "-c", "uvicorn twm.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY twm ./twm

EXPOSE 8000

CMD ["sh", "-c", "uvicorn twm.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

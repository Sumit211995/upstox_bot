FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# state/ logs/ data/ live on the server disk (paper records + candle cache)
VOLUME ["/app/state", "/app/logs", "/app/data"]
CMD ["python", "bot.py"]

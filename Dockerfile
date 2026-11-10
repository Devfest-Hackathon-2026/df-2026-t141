FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_ENV=production
# URLにユーザーIDが入るためアクセスログは出さない
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --proxy-headers --forwarded-allow-ips='*'"]

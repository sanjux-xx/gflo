FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ /app/
COPY site/ /site/

# The storefront HTML lives outside the app folder; DATA_DIR should be a mounted
# volume on Render / Northflank so the database and uploads survive redeploys.
ENV SITE_DIR=/site DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8000
# --no-proxy-headers: uvicorn otherwise rewrites the client address from an
# X-Forwarded-For it trusts by default, letting clients spoof their IP past the
# login rate limiter. Set FORWARDED_ALLOW_IPS (and TRUSTED_PROXIES) when you
# really are behind a reverse proxy.
CMD ["sh", "-c", "python scripts/bootstrap.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} ${FORWARDED_ALLOW_IPS:+--proxy-headers --forwarded-allow-ips=$FORWARDED_ALLOW_IPS} ${FORWARDED_ALLOW_IPS:---no-proxy-headers}"]

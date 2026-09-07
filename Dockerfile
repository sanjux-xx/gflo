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
# Proxy headers: uvicorn trusts X-Forwarded-For by default and rewrites the
# client address from it, which lets a caller spoof its IP past the login rate
# limiter. So it is OFF unless FORWARDED_ALLOW_IPS names your real proxy.
#
# NB: this used an ${VAR:+...} / ${VAR:-...} pair before, which was wrong —
# ${VAR:-default} returns VAR'S VALUE when VAR is set, so a configured
# FORWARDED_ALLOW_IPS was appended a second time as a bare argument and uvicorn
# exited with "Got unexpected extra argument". An if/else avoids that entirely.
CMD ["sh", "-c", "python scripts/bootstrap.py && if [ -n \"$FORWARDED_ALLOW_IPS\" ]; then exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=\"$FORWARDED_ALLOW_IPS\"; else exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-proxy-headers; fi"]

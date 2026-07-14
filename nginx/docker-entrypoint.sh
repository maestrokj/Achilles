#!/bin/sh
set -e

DOMAIN="${DOMAIN:-localhost}"

envsubst '${DOMAIN}' < /etc/nginx/nginx.conf.template > /tmp/nginx.conf

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    sed -i '/# --- HTTP-FALLBACK-BEGIN ---/,/# --- HTTP-FALLBACK-END ---/d' /tmp/nginx.conf
    echo "SSL certs found for ${DOMAIN} — HTTPS enabled"
else
    sed -i '/# --- REDIRECT-BEGIN ---/,/# --- REDIRECT-END ---/d' /tmp/nginx.conf
    sed -i '/# --- HTTPS-BEGIN ---/,/# --- HTTPS-END ---/d' /tmp/nginx.conf
    echo "No SSL certs for ${DOMAIN} — HTTP-only mode"
    echo "Run: docker compose exec certbot certbot certonly --webroot -w /var/www/certbot -d ${DOMAIN}"
    echo "Then: docker compose restart nginx"
fi

exec nginx -c /tmp/nginx.conf -g 'daemon off;'

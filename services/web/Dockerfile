# Static UI + hardened reverse proxy in front of the API (non-root nginx, port 8080).
FROM nginxinc/nginx-unprivileged:1.31.7-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e

USER 0
# Security fixes published after the pinned base (the CI image scan enforces this).
RUN apk upgrade --no-cache
USER 101

COPY --chown=101:101 nginx.conf /etc/nginx/nginx.conf
COPY --chown=101:101 proxy_common.conf /etc/nginx/proxy_common.conf
COPY --chown=101:101 site/ /usr/share/nginx/html/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD ["wget", "-qO-", "http://127.0.0.1:8080/healthz"]

# Caddy with the Sablier plugin (scale-to-zero). The plugin ships no pre-built Caddy, so
# it is compiled in with xcaddy; the runtime image is the official Alpine Caddy.
FROM caddy:2.11.9-builder@sha256:cda07fe9d82728c7ac44d844306d435e98131423dc7b114f05a8a8ecfff94707 AS builder
# The --replace pins pull in Go dependency security fixes newer than the plugin's
# go.sum (CI image scan: GO-2026-6860, GO-2026-6865).
RUN xcaddy build \
    --with github.com/sablierapp/sablier-caddy-plugin@v1.0.2 \
    --replace golang.org/x/net=golang.org/x/net@v0.62.0 \
    --replace github.com/klauspost/compress=github.com/klauspost/compress@v1.20.2

FROM caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0
# CI passes today's date: a changed build arg forces this layer to rebuild, so cached
# images still pick up security fixes published since the last build (at least daily).
ARG SECURITY_REFRESH=manual
RUN apk upgrade --no-cache   # security fixes newer than the pinned base
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
# The base gives caddy the cap_net_bind_service file capability (ports < 1024). With
# cap_drop: ALL + no-new-privileges the kernel refuses to exec a binary that requests a
# capability, and we listen on 8090 anyway: remove it.
RUN setcap -r /usr/bin/caddy 2>/dev/null || true
COPY Caddyfile /etc/caddy/Caddyfile
ENV XDG_DATA_HOME=/tmp/caddy-data XDG_CONFIG_HOME=/tmp/caddy-config
USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s CMD ["wget", "-qO-", "http://127.0.0.1:8090/gateway-health"]

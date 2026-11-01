# The Israeli exit for the scrapers: OpenVPN to CyberGhost Israel and an HTTP proxy on Railway's
# private network. Railway gives containers no /dev/net/tun, so OpenVPN 2.7 hands its packets to
# tunproxy (--dev-node unix:...), a userspace network stack, instead of a kernel tun device.
# Credentials come from Railway variables (see .env.example), never from the image.
FROM golang:1.23-bookworm AS build
WORKDIR /src
COPY tunproxy/ .
RUN CGO_ENABLED=0 go build -o /tunproxy .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://swupdate.openvpn.net/repos/repo-public.gpg | gpg --dearmor -o /usr/share/keyrings/openvpn.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/openvpn.gpg] https://build.openvpn.net/debian/openvpn/release/2.7 bookworm main" > /etc/apt/sources.list.d/openvpn.list \
    && apt-get update && apt-get install -y --no-install-recommends openvpn \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /tunproxy /usr/local/bin/tunproxy
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

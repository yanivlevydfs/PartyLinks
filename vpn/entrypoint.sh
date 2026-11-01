#!/bin/bash
# OpenVPN to CyberGhost Israel; tunproxy (started by OpenVPN) serves the HTTP proxy on VPN_PROXY_PORT.
# Every parameter is an environment variable (.env.example); certificates and key are base64 of the files.
set -euo pipefail

: "${VPN_REMOTE:?}" "${VPN_USERNAME:?}" "${VPN_PASSWORD:?}" "${VPN_CA_B64:?}" "${VPN_CERT_B64:?}" "${VPN_KEY_B64:?}"

dir=/etc/openvpn/client
mkdir -p "$dir"
echo "$VPN_CA_B64" | base64 -d > "$dir/ca.crt"
echo "$VPN_CERT_B64" | base64 -d > "$dir/client.crt"
echo "$VPN_KEY_B64" | base64 -d > "$dir/client.key"
printf '%s\n%s\n' "$VPN_USERNAME" "$VPN_PASSWORD" > "$dir/auth.txt"
chmod 600 "$dir"/*

# No routes or ifconfig: there is no kernel device; tunproxy owns the VPN address.
cat > "$dir/client.ovpn" <<EOF
client
remote $VPN_REMOTE ${VPN_PORT:-443}
proto ${VPN_PROTO:-udp}
dev tun
dev-node unix:/usr/local/bin/tunproxy
route-noexec
ifconfig-noexec
pull-filter ignore "route-ipv6"
pull-filter ignore "ifconfig-ipv6"
nobind
resolv-retry infinite
script-security 2
mssfix ${VPN_MSSFIX:-1250}
data-ciphers ${VPN_CIPHERS:-AES-256-GCM:AES-128-GCM:AES-256-CBC}
data-ciphers-fallback AES-256-CBC
auth ${VPN_AUTH:-SHA256}
remote-cert-tls server
auth-nocache
verb ${VPN_VERB:-3}
auth-user-pass $dir/auth.txt
# OpenVPN starts tunproxy with its own environment only: pass tunproxy's settings through setenv.
setenv VPN_PROXY_PORT "${VPN_PROXY_PORT:-8888}"
setenv VPN_ALLOW "${VPN_ALLOW:-fd00::/8 10.0.0.0/8 127.0.0.1 ::1}"
setenv VPN_MTU "${VPN_MTU:-1200}"
setenv VPN_DNS "${VPN_DNS:-1.1.1.1}"
ca $dir/ca.crt
cert $dir/client.crt
key $dir/client.key
EOF

echo "Starting OpenVPN $(openvpn --version | head -1 | cut -d' ' -f2) to $VPN_REMOTE"
exec openvpn --config "$dir/client.ovpn"

# Israeli VPN

Some Israeli sites refuse every IP outside Israel, including evomi and the free Israeli proxies. For these sites we run our own Israeli exit: a `vpn` service on Railway that keeps an OpenVPN tunnel open to CyberGhost Israel and serves an HTTP proxy on Railway's private network.

**Production only.** The VPN runs only on Railway production, and only for sources that set `vpn: true`. Localhost does not need it and has no VPN setting.

## Using it from a source

To send a source through the VPN, add one line to its block in `config/settings.yaml`:

```yaml
  newsource:
    enabled: true
    israel_proxy: true
    vpn: true  # our Israeli VPN only (Railway production)
```

- **`vpn: true`:** the source goes out only through the VPN. It does not fall back to the free proxies or the evomi pool.
- **No `vpn` line:** the source keeps its usual path: `proxied` (evomi) or `israel_proxy` (free Israeli proxies).
- **Localhost:** `PARTYLINKS_HTTP__ISRAEL_VPN` is not set there, so a `vpn: true` source takes its usual path.
- **Without a deploy:** you can also turn the field on or off in the admin **config** tab, which is saved in the database:

  ```yaml
  sources:
    newsource:
      vpn: true
  ```

Use `vpn: true` when a site refuses even the free Israeli proxies. This rule is also in [Adding a source](adding-a-source.md) (step 4) and in the `new-source` skill.

### How the per-source settings fit together

| Railway variable (`web`) | What it is | Source field |
| --- | --- | --- |
| `PARTYLINKS_HTTP__SOURCE_PROXY` | evomi pool (USA) | `proxied` (on by default) |
| `PARTYLINKS_HTTP__ISRAEL_PROXY` (+ `_FEED`) | free Israeli proxies | `israel_proxy: true` |
| `PARTYLINKS_HTTP__ISRAEL_VPN` | our VPN: `http://vpn.railway.internal:8888` | `vpn: true` |

The code is in `src/partylinks/scrapers/base.py` (`BaseScraper.__init__`). The field is `SourceSettings.vpn`, and the URL setting is `HttpSettings.israel_vpn`. The admin config view masks `israel_vpn` like the other proxy settings.

## Sources and results (2026-11-01)

| Source | `vpn` | Events through the VPN | Before the VPN |
| --- | --- | --- | --- |
| telaviv | true | 1,065 | blocked |
| livevents | true | 471 | blocked |
| pres_il | true | 359 | blocked |
| movieland | true | 263 | blocked |
| cinematheque_tlv | true | 80 | blocked |
| haifamuni | true | 79 | blocked |
| beatradar | true | 50 | blocked |
| yellowsubmarine | true | 34 | blocked on some runs |
| issta | true | 17 | blocked |
| onecity | true | 15 | 0 events |
| tiuli | true | 5 | worked |
| eventim | true | 0: Akamai challenge page (`sec-if-cpt`) | blocked |
| zappa | true | 0: same Akamai challenge (Eventim's system) | blocked |
| holon | true | 0: the site does not answer CyberGhost's IPs (TCP timeout) | failed |
| barak | no | 0 through the VPN (Akamai challenge). On its usual path (free proxies, then evomi): 24, partial, many pages empty | up to 83 |

eventim, zappa and holon, and barak's empty pages, need a residential Israeli IP. A VPN IP is a datacenter IP, so it will not open them.

## Architecture

```
web (scraper) --HTTP CONNECT--> vpn.railway.internal:8888 --tunproxy (userspace TCP/IP)--> OpenVPN --UDP--> CyberGhost Israel --> site
                 private network, IPv4 + IPv6
```

- **No tun device.** Railway gives containers no `/dev/net/tun`, so a normal OpenVPN client, Gluetun or `ip`/`tun0` scripts cannot work there. OpenVPN 2.7 instead starts our program `tunproxy` with `--dev-node unix:/usr/local/bin/tunproxy` and hands it a socket that carries one IP packet per message.
- **tunproxy** (`vpn/tunproxy/main.go`) runs gVisor's netstack, a TCP/IP stack in user space, from `golang.zx2c4.com/wireguard/tun/netstack`. It serves an HTTP proxy (CONNECT for HTTPS, plain HTTP too) that dials out through the tunnel.
- **IPv4 only in the tunnel.** The VPN gives an IPv4 address only, so every dial uses `tcp4`. A site's IPv6 address would hang.
- **Dual-stack proxy side.** The proxy listens on `[::]:8888`. Railway's private network is dual-stack: callers come from `fd12::/16` (IPv6) or `10.x` (IPv4). The `[::]` listener reports IPv4 callers as `::ffff:10.x`, so their addresses are unmapped before the allow-list check.
- **TLS end to end.** TLS runs directly between the scraper and the site. The VPN never sees the content.

### Files

| File | Role |
| --- | --- |
| `vpn/Dockerfile` | Builds tunproxy (Go) and installs OpenVPN 2.7 from the OpenVPN apt repo |
| `vpn/entrypoint.sh` | Writes the certificates and `client.ovpn` from the variables, then runs OpenVPN in a reconnect loop |
| `vpn/tunproxy/main.go` | The userspace device, the HTTP proxy, logs, liveness and exit checks |
| `vpn/tunproxy/main_test.go` | Tests for the dual-stack allow-list, source parsing and ipinfo parsing (`go test .`) |
| `vpn/.env.example` | Every variable with an explanation. The real `vpn/.env` is git-ignored |
| `vpn/.gitattributes` | Keeps LF line endings, because `entrypoint.sh` runs on Linux |

## Railway setup

- **Service:** `vpn` in project PartyLinks, environment production, region EU West, 1 replica.
- **Unexposed.** The service has no public domain. It is reachable only as `vpn.railway.internal` (IPv4 and IPv6). Don't generate a domain for it.
- **No git source.** The service is deployed from the folder:

  ```sh
  cd vpn && railway up . --path-as-root --service vpn --ci
  ```

  A push to master does not redeploy `vpn`; only `railway up` does. The other way round, a `vpn` deploy restarts the tunnel for about 10 seconds and breaks scrapes that are running at that moment.
- **Restart policy:** the Railway default (on failure). Reconnecting is handled inside the container, see below.
- **web:** `PARTYLINKS_HTTP__ISRAEL_VPN=http://vpn.railway.internal:8888`, set on production only.

### Variables of the `vpn` service

See `vpn/.env.example` for every variable. Mark these as secrets: `VPN_USERNAME`, `VPN_PASSWORD`, `VPN_CA_B64`, `VPN_CERT_B64`, `VPN_KEY_B64`. The CyberGhost values come from "Configure routers or other devices", OpenVPN, Israel.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VPN_REMOTE` / `VPN_PORT` / `VPN_PROTO` | `87-1-il.cg-dialup.net` / 443 / udp | CyberGhost Israel server |
| `VPN_PROXY_PORT` | 8888 | The proxy port on the private network |
| `VPN_ALLOW` | `fd00::/8 10.0.0.0/8 127.0.0.1 ::1` | Who may use the proxy (IPv6 + IPv4 private network) |
| `VPN_MTU` | 1200 | The MTU inside tunproxy. Railway's `railnet0` is 1316, and OpenVPN adds about 52 bytes |
| `VPN_MSSFIX` | 1250 | OpenVPN's mssfix |
| `VPN_DNS` | 1.1.1.1 | DNS inside the tunnel |
| `VPN_CHECK_HOST` / `VPN_CHECK_SECONDS` / `VPN_CHECK_FAILS` | `1.1.1.1:443` / 60 / 3 | Liveness check (see below) |
| `VPN_RETRY_SECONDS` | 5 | Pause before reconnecting |

**OpenVPN starts tunproxy with its own environment only.** tunproxy's variables (`VPN_PROXY_PORT`, `VPN_ALLOW`, `VPN_MTU`, `VPN_DNS`, `VPN_CHECK_*`) therefore reach it through `setenv` lines in `client.ovpn`, which `entrypoint.sh` writes. A new tunproxy variable needs a new `setenv` line there. Without one, tunproxy silently uses its default. That is how the MTU stayed at 1500: large TLS handshakes hung, and haifa, cintlv and others failed.

## Logs

tunproxy writes JSON lines. Railway turns their fields into filters in the vpn service's Deploy Logs:

| Filter | What you see |
| --- | --- |
| `@source:telaviv` | Every tunnel one source opened: host, `dial_ms`, `ms`, `sent_bytes`, `got_bytes` |
| `@level:error` | Failed connections (`connect failed`, with the error) and failed exit checks |
| `@level:warn` | Tunnels that got 0 bytes back (a hung TLS handshake or a reset), failed liveness checks, refused callers |
| `@event:stats` | One line a minute: `requests`, `failed`, `active` (open tunnels), bytes each way. Nothing when the proxy is idle |
| `@event:exit` | Every 10 minutes: the IP and country the sites see (should be `IL`) |
| `@event:reconnect` | Every reconnect and its reason (`liveness`, `country`, OpenVPN exit code) |
| `@event:start` | tunproxy started: VPN IP, MTU, DNS, allow-list |

- **Source name.** A source names itself as the proxy user (`http://telaviv@vpn.railway.internal:8888`). `base.py` adds the name, and tunproxy reads it from `Proxy-Authorization`. It is a label, not a password.
- **No HTTP status for HTTPS.** TLS hides the site's answer, so a CONNECT log line has the host, timing and bytes, but no HTTP status. The scraper's own log and the run report (`scripts/run_sources.py --runs <source>`) have the site's status.

From the CLI:

```sh
railway logs --service vpn --json | grep '"event":"stats"' | tail -5
railway logs --service vpn --json | grep '"source":"telaviv"' | tail
```

## Failure handling and reconnecting

| Failure | What happens |
| --- | --- |
| Server silent for 60 s | `ping-exit 60`: OpenVPN exits, and the loop in `entrypoint.sh` reconnects after `VPN_RETRY_SECONDS`. The server's pushed `ping-restart` is filtered out, so there is a single recovery path with a fresh tunproxy |
| Tunnel dead but OpenVPN unaware | tunproxy dials `VPN_CHECK_HOST` through the tunnel every `VPN_CHECK_SECONDS`. After `VPN_CHECK_FAILS` failures in a row it stops OpenVPN, and the loop reconnects |
| Exit outside Israel | The exit check (every 10 min, ipinfo.io) stops OpenVPN, and the loop reconnects (CyberGhost may hand out another server) |
| tunproxy crashes | It stops OpenVPN (its parent) before it exits, so no tunnel stays up without a proxy. The loop reconnects |
| OpenVPN exits (login refused, server down) | The loop reconnects after `VPN_RETRY_SECONDS`, forever. The container does not exit |
| Container exits anyway | Railway's restart policy starts it again |

A dead VPN does not make a `vpn: true` source fall back to another path. The run fails with a proxy error and the next scheduled run tries again. This is on purpose: those sites refuse every other IP anyway.

## Checking it

From inside `web`, not from outside: the proxy is internal only.

```sh
railway ssh --service web -- python -c "import httpx; print(httpx.get('http://ipinfo.io/country', proxy='http://check@vpn.railway.internal:8888', timeout=25).text)"
```

The answer should be `IL`. Then look for `source=check` in the vpn logs. To test both address families, resolve `vpn.railway.internal` with `socket.getaddrinfo` for `AF_INET` and `AF_INET6`, and pass each address as the proxy.

Run sources and see their results:

```sh
railway run --service web -- python scripts/run_sources.py telaviv onecity
railway run --service web -- python scripts/run_sources.py --runs telaviv
railway run --service web -- python scripts/run_sources.py --report 24
```

Tests: `cd vpn/tunproxy && go test .`, plus `tests/test_resilience.py::test_israel_vpn_replaces_every_other_proxy` for the source side.

## Lessons from building it

- **MTU.** Small pages worked while big TLS handshakes (Chrome's ClientHello, large certificate chains) hung. Railway's network MTU is 1316, so the tunnel MTU must stay at or below about 1260. We use 1200.
- **tunproxy's environment.** OpenVPN does not pass its environment on to tunproxy, so tunproxy settings must go through `setenv`. The startup log line shows the MTU actually in force. Check it after a change.
- **Partial commits of `settings.yaml`.** Staging only some of the file's hunks while another session had edits in it put `vpn: true` lines inside another block (movieland's `branches`). The YAML did not parse and the web deploy failed its healthcheck. Validate the staged file (`git show :config/settings.yaml`) with `yaml.safe_load` before you push.
- **Pushes and running scans.** A push redeploys `web`, and a `railway up` of `vpn` restarts the tunnel. Both kill runs that are in progress.

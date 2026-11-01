// Command tunproxy is OpenVPN's "tun" device in userspace: OpenVPN 2.7 starts it through
// `--dev-node unix:/usr/local/bin/tunproxy` (Railway gives containers no /dev/net/tun) and hands it a
// datagram socket (TUNTAP_SOCKET_FD) carrying one IP packet per message. gVisor's netstack turns
// those packets back into TCP connections, and an HTTP proxy on VPN_PROXY_PORT dials through them,
// so whatever goes through the proxy leaves from the VPN's Israeli IP.
//
// Logs are JSON lines (Railway turns their fields into filters: @source:onecity, @level:error,
// @event:stats). The scraper names itself as the proxy user: http://onecity@vpn.railway.internal:8888.
package main

import (
	"context"
	"encoding/base64"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"golang.zx2c4.com/wireguard/tun/netstack"
)

var log = slog.New(slog.NewJSONHandler(os.Stdout, nil))

// Counters for the minute's stats line.
var active, total, failed, bytesUp, bytesDown atomic.Int64

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// fatal stops OpenVPN too (our parent): entrypoint.sh then reconnects from scratch, so a dead
// proxy never sits on a live tunnel or the other way round.
func fatal(msg string, args ...any) {
	log.Error(msg, args...)
	if parent, err := os.FindProcess(os.Getppid()); err == nil {
		_ = parent.Signal(os.Interrupt)
	}
	os.Exit(1)
}

func main() {
	fd, err := strconv.Atoi(os.Getenv("TUNTAP_SOCKET_FD"))
	if err != nil {
		fatal("run by OpenVPN with --dev-node unix:... (TUNTAP_SOCKET_FD missing)", "event", "start")
	}
	sock, err := net.FilePacketConn(os.NewFile(uintptr(fd), "openvpn"))
	if err != nil {
		fatal("socket", "event", "start", "err", err)
	}
	local, err := netip.ParseAddr(os.Getenv("ifconfig_local"))
	if err != nil {
		fatal("ifconfig_local", "event", "start", "value", os.Getenv("ifconfig_local"), "err", err)
	}
	dns, err := netip.ParseAddr(env("VPN_DNS", "1.1.1.1"))
	if err != nil {
		fatal("VPN_DNS", "event", "start", "err", err)
	}
	// The tunnel's MTU: Railway's network is 1316, OpenVPN adds ~52 bytes, so 1200 fits.
	mtu, err := strconv.Atoi(env("VPN_MTU", "1200"))
	if err != nil {
		fatal("VPN_MTU", "event", "start", "err", err)
	}
	dev, tnet, err := netstack.CreateNetTUN([]netip.Addr{local}, []netip.Addr{dns}, mtu)
	if err != nil {
		fatal("netstack", "event", "start", "err", err)
	}

	go func() { // OpenVPN to netstack
		buf := make([]byte, 65535)
		for {
			n, _, err := sock.ReadFrom(buf)
			if err != nil {
				fatal("OpenVPN closed the socket", "event", "tunnel", "err", err)
			}
			if _, err := dev.Write([][]byte{buf[:n]}, 0); err != nil {
				log.Warn("inbound packet dropped", "event", "packet", "err", err)
			}
		}
	}()
	go func() { // netstack to OpenVPN
		bufs, sizes := [][]byte{make([]byte, 65535)}, []int{0}
		conn := sock.(*net.UnixConn)
		for {
			if _, err := dev.Read(bufs, sizes, 0); err != nil {
				fatal("netstack read", "event", "tunnel", "err", err)
			}
			if _, err := conn.Write(bufs[0][:sizes[0]]); err != nil {
				log.Warn("outbound packet dropped", "event", "packet", "err", err)
			}
		}
	}()

	// Railway's private network is dual stack: callers come from fd12::/16 or 10.x.
	var allow []netip.Prefix
	for _, s := range strings.Fields(env("VPN_ALLOW", "fd00::/8 10.0.0.0/8 127.0.0.1/32 ::1/128")) {
		p, err := netip.ParsePrefix(s)
		if a, aerr := netip.ParseAddr(s); aerr == nil { // a bare address
			p, err = netip.PrefixFrom(a, a.BitLen()), nil
		}
		if err != nil {
			fatal("VPN_ALLOW", "event", "start", "value", s, "err", err)
		}
		allow = append(allow, p)
	}

	listen := "[::]:" + env("VPN_PROXY_PORT", "8888")
	log.Info("proxy up", "event", "start", "vpn_ip", local.String(), "mtu", mtu, "dns", dns.String(), "listen", listen, "allow", env("VPN_ALLOW", ""))
	// The VPN gives an IPv4 address only: a site's IPv6 address would hang, so dial over IPv4.
	dial := func(ctx context.Context, _, address string) (net.Conn, error) {
		return tnet.DialContext(ctx, "tcp4", address)
	}
	go stats()
	go liveness(dial)
	go exitCheck(dial)
	server := &http.Server{Addr: listen, Handler: &proxy{dial: dial, allow: allow}, ReadHeaderTimeout: 30 * time.Second}
	fatal("proxy stopped", "event", "start", "err", server.ListenAndServe())
}

// stats logs the last minute's traffic; nothing when the proxy was idle.
func stats() {
	for range time.Tick(time.Minute) {
		n, f := total.Swap(0), failed.Swap(0)
		up, down := bytesUp.Swap(0), bytesDown.Swap(0)
		if n == 0 && active.Load() == 0 {
			continue
		}
		log.Info("last minute", "event", "stats", "requests", n, "failed", f, "active", active.Load(), "sent_bytes", up, "got_bytes", down)
	}
}

// liveness dials VPN_CHECK_HOST through the tunnel every VPN_CHECK_SECONDS; after VPN_CHECK_FAILS
// failures in a row the tunnel is dead though OpenVPN may not know yet: reconnect.
func liveness(dial func(ctx context.Context, network, address string) (net.Conn, error)) {
	host := env("VPN_CHECK_HOST", "1.1.1.1:443")
	every, _ := strconv.Atoi(env("VPN_CHECK_SECONDS", "60"))
	limit, _ := strconv.Atoi(env("VPN_CHECK_FAILS", "3"))
	for fails := 0; ; time.Sleep(time.Duration(max(every, 10)) * time.Second) {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		conn, err := dial(ctx, "tcp", host)
		cancel()
		if err == nil {
			conn.Close()
			if fails > 0 {
				log.Info("tunnel back", "event", "liveness", "after_fails", fails)
			}
			fails = 0
			continue
		}
		fails++
		log.Warn("tunnel check failed", "event", "liveness", "host", host, "fails", fails, "limit", limit, "err", err)
		if fails >= limit {
			fatal("tunnel dead: reconnecting", "event", "reconnect", "reason", "liveness")
		}
	}
}

// exitCheck asks ipinfo.io through the tunnel every 10 minutes which IP and country the sites see;
// an exit outside Israel reconnects (CyberGhost then hands out another server).
func exitCheck(dial func(ctx context.Context, network, address string) (net.Conn, error)) {
	client := &http.Client{Transport: &http.Transport{DialContext: dial}, Timeout: 20 * time.Second}
	for ; ; time.Sleep(10 * time.Minute) {
		resp, err := client.Get("https://ipinfo.io/json")
		if err != nil {
			log.Error("exit check failed", "event", "exit", "err", err) // liveness decides on reconnecting
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		resp.Body.Close()
		ip, country := field(body, "ip"), field(body, "country")
		if country != "" && country != "IL" {
			fatal("exit not in Israel: reconnecting", "event", "reconnect", "reason", "country", "ip", ip, "country", country)
		}
		log.Info("exit", "event", "exit", "ip", ip, "country", country)
	}
}

// field pulls "key": "value" out of ipinfo's flat JSON without a struct.
func field(body []byte, key string) string {
	_, rest, ok := strings.Cut(string(body), `"`+key+`": "`)
	if !ok {
		return ""
	}
	v, _, _ := strings.Cut(rest, `"`)
	return v
}

type proxy struct {
	dial  func(ctx context.Context, network, address string) (net.Conn, error)
	allow []netip.Prefix
}

// allowed: the [::] listener shows IPv4 callers as ::ffff:10.x, so unmap before matching.
func (p *proxy) allowed(remote string) bool {
	ap, err := netip.ParseAddrPort(remote)
	if err != nil {
		return false
	}
	ip := ap.Addr().Unmap().WithZone("")
	for _, pre := range p.allow {
		if pre.Contains(ip) {
			return true
		}
	}
	return false
}

// source is the scraper's name, sent as the proxy user (http://onecity@vpn...); "" when absent.
func source(r *http.Request) string {
	user, _, ok := strings.Cut(strings.TrimPrefix(r.Header.Get("Proxy-Authorization"), "Basic "), " ")
	if ok || user == "" {
		return ""
	}
	raw, err := base64.StdEncoding.DecodeString(user)
	if err != nil {
		return ""
	}
	name, _, _ := strings.Cut(string(raw), ":")
	return name
}

func (p *proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !p.allowed(r.RemoteAddr) {
		log.Warn("refused", "event", "refused", "client", r.RemoteAddr)
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if r.Method == http.MethodConnect {
		p.connect(w, r)
		return
	}
	if r.URL.Host == "" { // a request to the proxy itself: health check
		_, _ = io.WriteString(w, "ok\n")
		return
	}
	// Plain http:// through the proxy.
	src := source(r)
	r.RequestURI = ""
	r.Header.Del("Proxy-Connection")
	r.Header.Del("Proxy-Authorization")
	start := time.Now()
	total.Add(1)
	resp, err := (&http.Transport{DialContext: p.dial}).RoundTrip(r)
	if err != nil {
		failed.Add(1)
		log.Error("request failed", "event", "http", "source", src, "host", r.URL.Host, "ms", ms(start), "err", err)
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	for k, vs := range resp.Header {
		for _, v := range vs {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	n, _ := io.Copy(w, resp.Body)
	bytesDown.Add(n)
	log.Info("request", "event", "http", "source", src, "method", r.Method, "host", r.URL.Host, "path", r.URL.Path, "status", resp.StatusCode, "got_bytes", n, "ms", ms(start))
}

// connect tunnels HTTPS; TLS stays end to end between the scraper and the site, so the log has the
// host, how long the tunnel lived and the bytes each way, not the site's HTTP status.
func (p *proxy) connect(w http.ResponseWriter, r *http.Request) {
	src, start := source(r), time.Now()
	total.Add(1)
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	upstream, err := p.dial(ctx, "tcp", r.Host)
	cancel()
	if err != nil {
		failed.Add(1)
		log.Error("connect failed", "event", "connect", "source", src, "host", r.Host, "ms", ms(start), "err", err)
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	dialMs := ms(start)
	client, buf, err := w.(http.Hijacker).Hijack()
	if err != nil {
		upstream.Close()
		return
	}
	active.Add(1)
	defer active.Add(-1)
	_, _ = client.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	if n := buf.Reader.Buffered(); n > 0 { // bytes the client sent right after CONNECT
		data, _ := buf.Reader.Peek(n)
		_, _ = upstream.Write(data)
	}
	var sent, got int64
	done := make(chan struct{}, 2)
	go func() { sent, _ = io.Copy(upstream, client); done <- struct{}{} }()
	go func() { got, _ = io.Copy(client, upstream); done <- struct{}{} }()
	<-done // either side hung up
	client.Close()
	upstream.Close()
	<-done
	bytesUp.Add(sent)
	bytesDown.Add(got)
	level := slog.LevelInfo
	if got == 0 { // the site never answered: a TLS handshake that hung or a reset
		level = slog.LevelWarn
	}
	log.Log(context.Background(), level, "tunnel", "event", "connect", "source", src, "host", r.Host, "dial_ms", dialMs, "ms", ms(start), "sent_bytes", sent, "got_bytes", got)
}

func ms(t time.Time) int64 { return time.Since(t).Milliseconds() }

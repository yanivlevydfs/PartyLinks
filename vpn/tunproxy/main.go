// Command tunproxy is OpenVPN's "tun" device in userspace: OpenVPN 2.7 starts it through
// `--dev-node unix:/usr/local/bin/tunproxy` (Railway gives containers no /dev/net/tun) and hands it a
// datagram socket (TUNTAP_SOCKET_FD) carrying one IP packet per message. gVisor's netstack turns
// those packets back into TCP connections, and an HTTP proxy on VPN_PROXY_PORT dials through them,
// so whatever goes through the proxy leaves from the VPN's Israeli IP.
package main

import (
	"context"
	"io"
	"log"
	"net"
	"net/http"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"time"

	"golang.zx2c4.com/wireguard/tun/netstack"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	fd, err := strconv.Atoi(os.Getenv("TUNTAP_SOCKET_FD"))
	if err != nil {
		log.Fatal("tunproxy: run by OpenVPN with --dev-node unix:... (TUNTAP_SOCKET_FD missing)")
	}
	sock, err := net.FilePacketConn(os.NewFile(uintptr(fd), "openvpn"))
	if err != nil {
		log.Fatalf("tunproxy: socket: %v", err)
	}
	local, err := netip.ParseAddr(os.Getenv("ifconfig_local"))
	if err != nil {
		log.Fatalf("tunproxy: ifconfig_local %q: %v", os.Getenv("ifconfig_local"), err)
	}
	dns, err := netip.ParseAddr(env("VPN_DNS", "1.1.1.1"))
	if err != nil {
		log.Fatalf("tunproxy: VPN_DNS: %v", err)
	}
	// The tunnel's MTU; OpenVPN's mssfix keeps TCP segments small enough for the path.
	mtu, err := strconv.Atoi(env("VPN_MTU", "1500"))
	if err != nil {
		log.Fatalf("tunproxy: VPN_MTU: %v", err)
	}
	dev, tnet, err := netstack.CreateNetTUN([]netip.Addr{local}, []netip.Addr{dns}, mtu)
	if err != nil {
		log.Fatalf("tunproxy: netstack: %v", err)
	}

	go func() { // OpenVPN to netstack
		buf := make([]byte, 65535)
		for {
			n, _, err := sock.ReadFrom(buf)
			if err != nil {
				log.Fatalf("tunproxy: OpenVPN closed the socket: %v", err)
			}
			if _, err := dev.Write([][]byte{buf[:n]}, 0); err != nil {
				log.Printf("tunproxy: inbound packet dropped: %v", err)
			}
		}
	}()
	go func() { // netstack to OpenVPN
		bufs, sizes := [][]byte{make([]byte, 65535)}, []int{0}
		conn := sock.(*net.UnixConn)
		for {
			if _, err := dev.Read(bufs, sizes, 0); err != nil {
				log.Fatalf("tunproxy: netstack: %v", err)
			}
			if _, err := conn.Write(bufs[0][:sizes[0]]); err != nil {
				log.Printf("tunproxy: outbound packet dropped: %v", err)
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
			log.Fatalf("tunproxy: VPN_ALLOW %q: %v", s, err)
		}
		allow = append(allow, p)
	}

	listen := "[::]:" + env("VPN_PROXY_PORT", "8888")
	log.Printf("tunproxy: VPN address %s, HTTP proxy on %s for %v", local, listen, allow)
	// The VPN gives an IPv4 address only: a site's IPv6 address would hang, so dial over IPv4.
	dial := func(ctx context.Context, _, address string) (net.Conn, error) {
		return tnet.DialContext(ctx, "tcp4", address)
	}
	server := &http.Server{Addr: listen, Handler: &proxy{dial: dial, allow: allow}, ReadHeaderTimeout: 30 * time.Second}
	log.Fatal(server.ListenAndServe())
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

func (p *proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !p.allowed(r.RemoteAddr) {
		log.Printf("tunproxy: refused %s", r.RemoteAddr)
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
	r.RequestURI = ""
	r.Header.Del("Proxy-Connection")
	resp, err := (&http.Transport{DialContext: p.dial}).RoundTrip(r)
	if err != nil {
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
	_, _ = io.Copy(w, resp.Body)
}

// connect tunnels HTTPS; TLS stays end to end between the scraper and the site.
func (p *proxy) connect(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	upstream, err := p.dial(ctx, "tcp", r.Host)
	cancel()
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	client, buf, err := w.(http.Hijacker).Hijack()
	if err != nil {
		upstream.Close()
		return
	}
	_, _ = client.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	if n := buf.Reader.Buffered(); n > 0 { // bytes the client sent right after CONNECT
		data, _ := buf.Reader.Peek(n)
		_, _ = upstream.Write(data)
	}
	done := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(upstream, client); done <- struct{}{} }()
	go func() { _, _ = io.Copy(client, upstream); done <- struct{}{} }()
	<-done // either side hung up
	client.Close()
	upstream.Close()
}

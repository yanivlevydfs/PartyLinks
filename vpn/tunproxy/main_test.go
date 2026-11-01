package main

import (
	"net/netip"
	"testing"
)

func TestAllowedDualStack(t *testing.T) {
	p := &proxy{allow: []netip.Prefix{netip.MustParsePrefix("fd00::/8"), netip.MustParsePrefix("10.0.0.0/8")}}
	for remote, want := range map[string]bool{
		"[fd12:54e6:c739:1::5]:4000": true,  // Railway IPv6
		"[::ffff:10.233.1.2]:4000":   true,  // Railway IPv4 seen through the [::] listener
		"10.233.1.2:4000":            true,  // plain IPv4
		"[2001:db8::1]:4000":         false, // public IPv6
		"[::ffff:8.8.8.8]:4000":      false, // public IPv4
		"garbage":                    false,
	} {
		if got := p.allowed(remote); got != want {
			t.Errorf("allowed(%q) = %v, want %v", remote, got, want)
		}
	}
}

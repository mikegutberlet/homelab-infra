#!/usr/bin/env python3
"""Which PIA addresses answer an OpenVPN hello on which UDP port (read-only, standard library).

Run it from the VM's own network, not inside gluetun (its firewall blocks everything but the
tunnel):  ssh goobs@192.168.1.51 python3 - < docs/ovpn_probe.py
Sends one OpenVPN client hard-reset packet per address and port; 0x40 is the server's reply,
"-" is no reply in 6 s. 2026-11-20: PIA's new Vancouver servers answered on 8080/853/123/53
but only one of 17 on 1197/1198, which is why gluetun dials 8080 (stacks/media/compose.yaml).
Addresses: the "UDPv4 link remote" lines of `docker logs media-gluetun-1`, or the region's
ovpnudp entries in https://serverlist.piaservers.net/vpninfo/servers/v6
"""
import os
import select
import socket
import time

IPS = ["107.182.124.47", "107.182.124.104", "107.182.123.181", "107.182.124.112", "107.182.124.208",
       "107.182.124.82", "107.182.124.42", "107.182.124.148", "107.182.123.111", "107.182.123.48"]
PORTS = [1197, 1198, 8080, 853, 123, 53]

socks = {}
for ip in IPS:
    for port in PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setblocking(False)
        s.sendto(b"\x38" + os.urandom(8) + b"\x00" + b"\x00\x00\x00\x00", (ip, port))
        socks[s] = (ip, port)
        time.sleep(0.02)
got = {}
end = time.time() + 6
while time.time() < end and len(got) < len(socks):
    ready, _, _ = select.select(list(socks), [], [], 0.3)
    for s in ready:
        try:
            got[socks[s]] = "0x%02x" % s.recvfrom(2000)[0][0]
        except OSError:
            got[socks[s]] = "err"
print("%-16s" % "ip" + "".join("%7d" % p for p in PORTS))
for ip in IPS:
    print("%-16s" % ip + "".join("%7s" % got.get((ip, p), "-") for p in PORTS))

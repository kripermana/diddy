"""Render config Kea DHCPv4 dan control socket."""

import json
import socket

from ..config import C, KEA_MYSQL
from ..db.connection import q
from ..ipam.networks import all_nets, smallest_net


def render_kea():
    nets = all_nets()
    dh = [h for h in q("SELECT * FROM hosts WHERE configure_dhcp=1 AND mac!=''")]
    subnets = []
    for n in nets:
        if not n["dhcp_enabled"] or n["_n"].version != 4:
            continue
        opts = []
        if n["gateway"]:
            opts.append({"name": "routers", "data": n["gateway"]})
        if n["dns_servers"]:
            opts.append({"name": "domain-name-servers", "data": n["dns_servers"]})
        if n["domain_name"]:
            opts.append({"name": "domain-name", "data": n["domain_name"]})
        res = []
        for h in dh:
            b = smallest_net(h["ip"], nets, dhcp_only=True)
            if b and b["id"] == n["id"]:
                res.append({"hw-address": h["mac"], "ip-address": h["ip"], "hostname": h["fqdn"].split(".")[0]})
        s = {"id": n["id"], "subnet": n["cidr"], "valid-lifetime": n["lease_time"],
             "pools": [{"pool": f"{r['start_ip']} - {r['end_ip']}"}
                       for r in q("SELECT * FROM ranges WHERE network_id=? ORDER BY id", (n["id"],))],
             "option-data": opts, "reservations": res}
        if n["comment"]:
            s["user-context"] = {"comment": n["comment"]}
        subnets.append(s)
    ifaces = [i.strip() for i in C["dhcp_interfaces"].split(",") if i.strip()] or ["*"]
    return {"Dhcp4": {
        "interfaces-config": {"interfaces": ifaces},
        "control-socket": {"socket-type": "unix", "socket-name": C["kea_socket"]},
        "lease-database": ({"type": "mysql", "name": C["kea_db_name"], "host": C["kea_db_host"],
                            "port": int(C["kea_db_port"]), "user": C["kea_db_user"],
                            "password": C["kea_db_password"], "reconnect-wait-time": 3000,
                            "max-reconnect-tries": 100, "on-fail": "serve-retry-continue"}
                           if KEA_MYSQL else
                           {"type": "memfile", "persist": True, "name": C["kea_lease_file"], "lfc-interval": 3600}),
        "valid-lifetime": 86400,
        "subnet4": subnets,
    }}


def kea_command(cmd):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect(C["kea_socket"])
    s.sendall(json.dumps({"command": cmd}).encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
        try:
            json.loads(data)
            break
        except ValueError:
            continue
    s.close()
    r = json.loads(data)
    return r[0] if isinstance(r, list) else r

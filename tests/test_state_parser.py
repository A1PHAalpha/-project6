"""状态事实解析器单元测试。"""
from collector.state_parser import (
    normalize_interface_name,
    observations_from_item,
    parse_access_lists,
    parse_cdp_neighbors,
    parse_interface_brief,
    parse_route_table,
    parse_vlan_brief,
)


def test_normalize_interface_names():
    assert normalize_interface_name("Eth 0/0") == "Ethernet0/0"
    assert normalize_interface_name("Et0/1") == "Ethernet0/1"
    assert normalize_interface_name("Ethernet0/0.10") == "Ethernet0/0.10"
    assert normalize_interface_name("Gi0/1") == "GigabitEthernet0/1"


def test_parse_interface_brief():
    raw = (
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "Ethernet0/0.10         10.10.10.1      YES NVRAM  up                    up\n"
        "Ethernet0/1            unassigned      YES NVRAM  administratively down down\n"
    )
    observations = parse_interface_brief(raw)
    assert len(observations) == 2
    assert observations[0]["entity_key"] == "Ethernet0/0.10"
    assert observations[0]["payload"]["ip_address"] == "10.10.10.1"
    assert observations[1]["payload"]["admin_status"] == "down"


def test_parse_route_vlan_acl_and_cdp():
    routes = parse_route_table(
        "S*    0.0.0.0/0 [1/0] via 172.16.1.1\n"
        "C        10.10.10.0/24 is directly connected, Ethernet0/0.10\n"
    )
    assert [route["entity_key"] for route in routes] == ["0.0.0.0/0", "10.10.10.0/24"]
    assert routes[0]["payload"]["next_hop"] == "172.16.1.1"

    vlans = parse_vlan_brief(
        "1    default       active    Et0/1, Et0/2\n"
        "10   VLAN0010      active    Et1/0\n"
    )
    assert vlans[0]["entity_key"] == "VLAN1"
    assert vlans[1]["payload"]["ports"] == ["Ethernet1/0"]

    acls = parse_access_lists(
        "Extended IP access list BLOCK_BRANCH\n"
        "    10 deny ip 192.168.20.0 0.0.0.255 any\n"
        "    20 permit ip any any\n"
    )
    assert acls[0]["entity_key"] == "BLOCK_BRANCH"
    assert acls[0]["payload"]["rules"][0]["action"] == "deny"

    neighbors = parse_cdp_neighbors(
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\n"
        "HQ-R1            Eth 0/0           171        R B         Linux Uni Eth 0/0\n"
    )
    assert neighbors[0]["payload"]["remote_device"] == "HQ-R1"
    assert neighbors[0]["payload"]["local_interface"] == "Ethernet0/0"


def test_observations_from_multiple_sources():
    interface = observations_from_item(
        {
            "command": "show ip interface brief",
            "capture_type": "interface",
            "raw_output": "Ethernet0/0 10.0.0.1 YES NVRAM up up\n",
            "parsed": {},
        }
    )
    ping = observations_from_item(
        {
            "command": "ping 10.0.0.2",
            "capture_type": "ping",
            "raw_output": "4 packets transmitted, 4 received, 0% packet loss",
            "parsed": {"target_ip": "10.0.0.2", "reachable": True},
        }
    )
    assert interface[0]["state_type"] == "interface"
    assert ping[0]["state_type"] == "reachability"
    assert ping[0]["payload"]["reachable"] is True

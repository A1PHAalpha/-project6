"""Write-command classification for audited terminal sessions."""

WRITE_PREFIXES = (
    "configure",
    "conf t",
    "interface ",
    "no shutdown",
    "shutdown",
    "write memory",
    "copy run",
    "vlan ",
    "switchport ",
    "ip route ",
    "access-list ",
    "router ",
    "erase ",
    "delete ",
    "reload",
    "undo ",
    "system-view",
    "commit",
    "save",
)


def is_write_command(device_type: str, command: str) -> bool:
    """Return True when a command may change device configuration.

    ``device_type`` is kept in the signature so platform-specific rules can be
    added without changing callers. The current prefix set is shared by the
    supported Cisco/Huawei/Linux device families.
    """
    text = (command or "").strip().lower()
    return any(text.startswith(prefix) for prefix in WRITE_PREFIXES)

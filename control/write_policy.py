"""Write-command classification for audited terminal sessions."""

READ_ONLY_COMMANDS = {
    "show",
    "display",
    "ping",
    "traceroute",
    "dir",
    "more",
    "exit",
    "quit",
    "terminal",
    "screen-length",
}

COMMAND_SEPARATORS = ("\n", "\r", ";", "&&", "||", "`", "$(")


def is_write_command(device_type: str, command: str) -> bool:
    """Return True unless the command is an explicitly known read-only action.

    This is deny-by-default: unrecognized commands, platform abbreviations,
    negations, shell operations, and multi-line/compound input are treated as
    writes and therefore require backup plus confirmation. ``device_type`` is
    kept for future platform-specific policy without changing callers.
    """
    text = (command or "").strip()
    if not text:
        return True
    if any(separator in text for separator in COMMAND_SEPARATORS):
        return True
    first = text.split(None, 1)[0].lower()
    return first not in READ_ONLY_COMMANDS

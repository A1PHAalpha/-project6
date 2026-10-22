// Keep this policy aligned with control/write_policy.py: deny by default.
const READ_ONLY_COMMANDS = new Set([
  "show",
  "display",
  "ping",
  "traceroute",
  "dir",
  "more",
  "exit",
  "quit",
  "terminal",
  "screen-length"
]);

const COMMAND_SEPARATORS = ["\n", "\r", ";", "&&", "||", "`", "$("];

export function looksLikeWriteCommand(command: string): boolean {
  const text = command.trim();
  if (!text) return true;
  if (COMMAND_SEPARATORS.some((separator) => text.includes(separator))) return true;
  const first = text.split(/\s+/, 1)[0].toLowerCase();
  return !READ_ONLY_COMMANDS.has(first);
}

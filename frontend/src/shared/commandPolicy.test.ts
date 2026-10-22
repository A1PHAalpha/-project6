import { describe, expect, it } from "vitest";

import { looksLikeWriteCommand } from "./commandPolicy";

describe("looksLikeWriteCommand", () => {
  it("treats known read-only commands as reads", () => {
    for (const command of ["show ip interface brief", "display version", "ping 10.0.0.1", "terminal length 0"]) {
      expect(looksLikeWriteCommand(command)).toBe(false);
    }
  });

  it("treats negations, abbreviations, platform and multi-line commands as writes", () => {
    for (const command of [
      "no shutdown",
      "no shut",
      "undo shutdown",
      "save",
      "wr mem",
      "sudo systemctl restart nginx",
      "show version\nconfigure terminal",
      "show version; reload"
    ]) {
      expect(looksLikeWriteCommand(command)).toBe(true);
    }
  });
});

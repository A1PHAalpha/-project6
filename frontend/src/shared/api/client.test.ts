import { describe, expect, it } from "vitest";

import { api } from "./client";

describe("session report URL", () => {
  it("encodes the session id for the v1 export route", () => {
    expect(api.sessionReportUrl("session/with space")).toBe(
      "/api/v1/sessions/session%2Fwith%20space/report"
    );
  });
});

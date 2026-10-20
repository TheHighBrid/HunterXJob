import { killSwitchRequest, REVIEW_ACTION_COPY } from "@/safety";

describe("kill switch requests", () => {
  it("always allows engaging", () => {
    expect(killSwitchRequest(true, false)).toMatchObject({ engaged: true });
  });

  it("refuses to build a disengage request without confirmation", () => {
    expect(killSwitchRequest(false, false)).toBeNull();
    expect(killSwitchRequest(false, true)).toMatchObject({ engaged: false, confirm: true });
  });
});

describe("review action copy", () => {
  it("tells the owner that approving never submits", () => {
    expect(REVIEW_ACTION_COPY.approve.body).toMatch(/never submits/);
  });
});

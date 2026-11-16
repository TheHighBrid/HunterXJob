import { MAX_DAILY_FREE_APPLICATIONS, useSubscription } from "../store/subscription";

describe("Subscription & Monetization Tier Engine", () => {
  beforeEach(() => {
    useSubscription.setState({
      plan: "free",
      licenseKey: "",
      dailyApplicationCount: 0,
      lastApplicationDate: new Date().toISOString().split("T")[0],
    });
  });

  test("initializes with Free tier and 2 daily application quota", () => {
    const state = useSubscription.getState();
    expect(state.plan).toBe("free");
    expect(state.canApply()).toBe(true);
    expect(state.canRunAutonomy()).toBe(false);
    expect(state.remainingToday()).toBe(MAX_DAILY_FREE_APPLICATIONS);
  });

  test("enforces max 2 daily applications on Free tier", () => {
    const state = useSubscription.getState();

    // 1st application
    expect(state.recordApplication()).toBe(true);
    expect(useSubscription.getState().remainingToday()).toBe(1);
    expect(useSubscription.getState().canApply()).toBe(true);

    // 2nd application
    expect(useSubscription.getState().recordApplication()).toBe(true);
    expect(useSubscription.getState().remainingToday()).toBe(0);
    expect(useSubscription.getState().canApply()).toBe(false);

    // 3rd application blocked
    expect(useSubscription.getState().recordApplication()).toBe(false);
    expect(useSubscription.getState().canApply()).toBe(false);
  });

  test("activates HunterX Pro with valid license key and unlocks unlimited access", () => {
    const state = useSubscription.getState();
    
    // Invalid key rejected
    expect(state.activatePro("INVALID-KEY")).toBe(false);
    expect(useSubscription.getState().plan).toBe("free");

    // Valid key accepted
    expect(state.activatePro("HUNTERX-PRO-2026")).toBe(true);
    const proState = useSubscription.getState();
    expect(proState.plan).toBe("pro");
    expect(proState.canApply()).toBe(true);
    expect(proState.canRunAutonomy()).toBe(true);
    expect(proState.remainingToday()).toBeGreaterThan(100);

    // Can record beyond 2 on Pro
    expect(proState.recordApplication()).toBe(true);
    expect(proState.recordApplication()).toBe(true);
    expect(proState.recordApplication()).toBe(true);
    expect(useSubscription.getState().canApply()).toBe(true);
  });
});

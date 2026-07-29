import { describe, expect, it } from "vitest";
import {
  isSkTelecomActive,
  skCongestionToDisplayLevel,
  skCongestionToPercent,
} from "../src/views/waveguardDashboard.js";

describe("SK → WaveGuard map level", () => {
  it("maps congestion levels to display levels", () => {
    expect(skCongestionToDisplayLevel(1)).toBe("안전");
    expect(skCongestionToDisplayLevel(2)).toBe("주의");
    expect(skCongestionToDisplayLevel(3)).toBe("주의");
    expect(skCongestionToDisplayLevel(4)).toBe("위험");
    expect(skCongestionToDisplayLevel(null)).toBe("데이터없음");
  });

  it("maps congestion levels to risk percent", () => {
    expect(skCongestionToPercent(1)).toBeLessThan(skCongestionToPercent(4));
  });

  it("treats connected and cached as active SK", () => {
    expect(isSkTelecomActive("connected")).toBe(true);
    expect(isSkTelecomActive("cached")).toBe(true);
    expect(isSkTelecomActive("idle")).toBe(false);
    expect(isSkTelecomActive("quota_exceeded")).toBe(false);
  });
});

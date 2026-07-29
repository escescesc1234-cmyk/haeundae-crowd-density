import { describe, expect, it } from "vitest";
import { calculateRawDensity } from "../src/density/calculator.js";
import { DEFAULT_THRESHOLDS } from "../src/config/thresholds.js";

describe("calculateRawDensity", () => {
  it("계산: 350명 / 100㎡ = 3.5", () => {
    const r = calculateRawDensity(350, 100);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.rawDensity).toBe(3.5);
  });

  it("0명/㎡", () => {
    const r = calculateRawDensity(0, 100);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.rawDensity).toBe(0);
  });

  it("면적 0 → 오류", () => {
    const r = calculateRawDensity(10, 0);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("INVALID_AREA");
  });

  it("면적 음수 → 오류", () => {
    const r = calculateRawDensity(10, -5);
    expect(r.ok).toBe(false);
  });

  it("인원 누락 → 데이터없음", () => {
    const r = calculateRawDensity(null, 100);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("MISSING_PEOPLE");
  });

  it("인원 음수 → 오류", () => {
    const r = calculateRawDensity(-1, 100);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.code).toBe("INVALID_PEOPLE");
  });

  it("인원 비정수 → 오류", () => {
    const r = calculateRawDensity(1.5, 100);
    expect(r.ok).toBe(false);
  });
});

describe("초기 참고 임계값", () => {
  it("기본값은 4 / 5 / 6", () => {
    expect(DEFAULT_THRESHOLDS.congestionStartDensity).toBe(4);
    expect(DEFAULT_THRESHOLDS.criticalDensity).toBe(5);
    expect(DEFAULT_THRESHOLDS.highRiskDensity).toBe(6);
  });
});

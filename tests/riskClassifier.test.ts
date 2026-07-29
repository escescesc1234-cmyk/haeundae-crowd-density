import { describe, expect, it } from "vitest";
import {
  classifyByDensityOnly,
  applyHysteresis,
} from "../src/density/hysteresis.js";
import { assessCriticalDensity } from "../src/density/riskClassifier.js";
import { DEFAULT_THRESHOLDS } from "../src/config/thresholds.js";

const T = DEFAULT_THRESHOLDS;

describe("필수 밀도 구간 등급 (4/5/6 초기 참고값)", () => {
  const cases: Array<[number, string, boolean]> = [
    [0, "안전", false],
    [3.9, "안전", false],
    [4.0, "혼잡", false],
    [4.9, "혼잡", false],
    [5.0, "혼잡", true],
    [5.9, "혼잡", true],
    [6.0, "위험", false],
    [7.0, "위험", false],
  ];

  for (const [density, level, critical] of cases) {
    it(`${density}명/㎡ → ${level}${critical ? " + 임계도달" : ""}`, () => {
      const risk = classifyByDensityOnly(density, T);
      expect(risk).toBe(level);
      const c = assessCriticalDensity(density, risk, T);
      expect(c.criticalDensityReached).toBe(critical);
      if (density === 5.9) {
        expect(c.approachingHighRisk).toBe(true);
        expect(c.actionsTriggered.length).toBeGreaterThan(0);
      }
    });
  }

  it("혼잡 시작 바로 아래 / 동일 / 임계 바로 아래 / 고위험 바로 아래", () => {
    expect(classifyByDensityOnly(3.999, T)).toBe("안전");
    expect(classifyByDensityOnly(4.0, T)).toBe("혼잡");
    expect(classifyByDensityOnly(4.999, T)).toBe("혼잡");
    expect(classifyByDensityOnly(5.999, T)).toBe("혼잡");
    expect(classifyByDensityOnly(6.0, T)).toBe("위험");
  });
});

describe("히스테리시스 및 급상승", () => {
  it("임계값 주변 진동 시 즉시 등급이 바뀌지 않음", () => {
    let state = {
      currentRiskLevel: "안전" as const,
      currentRiskSince: "2026-07-17T10:00:00.000Z",
    };

    const t1 = applyHysteresis({
      adjustedDensity: 4.05,
      measuredAt: "2026-07-17T10:00:05.000Z",
      thresholds: T,
      state,
    });
    expect(t1.riskLevel).toBe("안전");
    state = t1.state;

    const t2 = applyHysteresis({
      adjustedDensity: 3.95,
      measuredAt: "2026-07-17T10:00:08.000Z",
      thresholds: T,
      state,
    });
    expect(t2.riskLevel).toBe("안전");
  });

  it("상승 최소 지속 시간 후 혼잡으로 전환", () => {
    let state = {
      currentRiskLevel: "안전" as const,
      currentRiskSince: "2026-07-17T10:00:00.000Z",
    };

    const pending = applyHysteresis({
      adjustedDensity: 4.2,
      measuredAt: "2026-07-17T10:00:00.000Z",
      thresholds: T,
      state,
    });
    expect(pending.riskLevel).toBe("안전");
    state = pending.state;

    const upgraded = applyHysteresis({
      adjustedDensity: 4.2,
      measuredAt: "2026-07-17T10:00:11.000Z",
      thresholds: T,
      state,
    });
    expect(upgraded.riskLevel).toBe("혼잡");
  });

  it("고위험 크게 초과 시 즉시 위험", () => {
    const result = applyHysteresis({
      adjustedDensity: 8.0,
      measuredAt: "2026-07-17T10:00:00.000Z",
      thresholds: T,
      state: {
        currentRiskLevel: "안전",
        currentRiskSince: "2026-07-17T09:00:00.000Z",
      },
    });
    expect(result.riskLevel).toBe("위험");
    expect(result.immediateAlert).toBe(true);
  });

  it("급상승 시 즉시 위험", () => {
    const result = applyHysteresis({
      adjustedDensity: 4.5,
      measuredAt: "2026-07-17T10:00:00.000Z",
      thresholds: T,
      densityRisePerSecond: 0.2,
      state: {
        currentRiskLevel: "안전",
        currentRiskSince: "2026-07-17T09:00:00.000Z",
      },
    });
    expect(result.riskLevel).toBe("위험");
    expect(result.immediateAlert).toBe(true);
  });

  it("위험→혼잡 하락은 hysteresisMargin 이하 + 지속 시간 필요", () => {
    let state = {
      currentRiskLevel: "위험" as const,
      currentRiskSince: "2026-07-17T10:00:00.000Z",
    };

    // 6.0 - 0.2 = 5.8 초과이면 여전히 위험 후보
    const stillHigh = applyHysteresis({
      adjustedDensity: 5.9,
      measuredAt: "2026-07-17T10:00:05.000Z",
      thresholds: T,
      state,
    });
    expect(stillHigh.riskLevel).toBe("위험");

    const startDown = applyHysteresis({
      adjustedDensity: 5.7,
      measuredAt: "2026-07-17T10:01:00.000Z",
      thresholds: T,
      state,
    });
    expect(startDown.riskLevel).toBe("위험");
    state = startDown.state;

    const downgraded = applyHysteresis({
      adjustedDensity: 5.7,
      measuredAt: "2026-07-17T10:01:20.000Z",
      thresholds: T,
      state,
    });
    expect(downgraded.riskLevel).toBe("혼잡");
  });
});

import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { NotificationService } from "../src/notification/service.js";
import { evaluateNotificationEvent } from "../src/notification/evaluator.js";
import { determineNotificationPriority } from "../src/notification/priority.js";
import { preventDuplicateNotification } from "../src/notification/duplicate.js";
import { generateTouristMessage } from "../src/notification/touristMessage.js";
import { generateManagerMessage } from "../src/notification/managerMessage.js";
import {
  selectTouristRecipients,
  selectManagerRecipients,
} from "../src/notification/recipients.js";
import { buildNotificationInput } from "../src/notification/adapter.js";
import { loadNotificationPolicy } from "../src/notification/config.js";
import type {
  DensityAnalysisResult,
  RiskLevel,
} from "../src/types/index.js";
import type {
  ManagerUserProfile,
  TouristUserProfile,
} from "../src/notification/types.js";
import { nextEventId } from "../src/notification/storage.js";

function baseAnalysis(
  overrides: Partial<DensityAnalysisResult> & { zoneId?: string } = {},
): DensityAnalysisResult {
  const measuredAt = overrides.measuredAt ?? new Date().toISOString();
  return {
    zoneId: overrides.zoneId ?? "GWANGALLI-ZONE-CENTER",
    zoneName: overrides.zoneName ?? "출입구 주변 A",
    zoneType: overrides.zoneType ?? "entrance",
    effectiveAreaSquareMeters: overrides.effectiveAreaSquareMeters ?? 160,
    detectedPeople: overrides.detectedPeople ?? 640,
    rawDensity: overrides.rawDensity ?? 4.0,
    adjustedDensity: overrides.adjustedDensity ?? 4.0,
    riskLevel: overrides.riskLevel ?? "혼잡",
    criticalDensityReached: overrides.criticalDensityReached ?? false,
    approachingHighRisk: overrides.approachingHighRisk ?? false,
    trend: overrides.trend ?? "증가",
    thresholds: overrides.thresholds ?? {
      congestionStartDensity: 4,
      criticalDensity: 5,
      highRiskDensity: 6,
      hysteresisMargin: 0.2,
      source: "global",
    },
    reason: overrides.reason ?? "테스트",
    confidence: overrides.confidence ?? 0.92,
    lowConfidence: overrides.lowConfidence ?? false,
    requiresManagerReview: overrides.requiresManagerReview ?? true,
    measuredAt,
    dataSource: overrides.dataSource ?? "test",
    isTestData: true,
    recommendedActions: [],
    touristSummary: {
      zoneId: "GWANGALLI-ZONE-CENTER",
      zoneName: "출입구 주변 A",
      riskLevel: overrides.riskLevel ?? "혼잡",
      riskLabel: "혼잡",
      congestionHint: "",
      recommendedAction: "",
      safeDirectionHint: "",
      colorCode: "#f9a825",
      icon: "!",
      updatedAt: measuredAt,
      disclaimer: "",
    },
    adminSummary: {
      zoneId: "GWANGALLI-ZONE-CENTER",
      zoneName: "출입구 주변 A",
      effectiveAreaSquareMeters: 160,
      detectedPeople: 640,
      rawDensity: 4,
      adjustedDensity: 4,
      riskLevel: overrides.riskLevel ?? "혼잡",
      thresholds: {
        congestionStartDensity: 4,
        criticalDensity: 5,
        highRiskDensity: 6,
        hysteresisMargin: 0.2,
        source: "global",
      },
      criticalDensityReached: false,
      approachingHighRisk: false,
      trend: "증가",
      reason: "",
      confidence: 0.92,
      requiresManagerReview: true,
      densityHistory: [],
      auxiliaryAlerts: [],
      actionsTriggered: [],
      errors: [],
      warnings: [],
      canManualOverride: true,
      canSendAlert: true,
      canRecordFalsePositive: true,
    },
    auxiliaryAlerts: overrides.auxiliaryAlerts ?? [],
    densityBasedSafeButOtherRisks:
      overrides.densityBasedSafeButOtherRisks ?? false,
    errors: overrides.errors ?? [],
    warnings: overrides.warnings ?? [],
    actionsTriggered: overrides.actionsTriggered ?? [],
  };
}

const tourists: TouristUserProfile[] = [
  {
    userId: "t-in-zone",
    locationConsent: "granted",
    pushEnabled: true,
    currentZoneId: "GWANGALLI-ZONE-CENTER",
    insideRiskZone: true,
    latitude: 35.1598,
    longitude: 129.1615,
  },
  {
    userId: "t-approaching",
    locationConsent: "granted",
    pushEnabled: true,
    headingTowardZoneId: "GWANGALLI-ZONE-CENTER",
    latitude: 35.1595,
    longitude: 129.161,
  },
  {
    userId: "t-no-location",
    locationConsent: "denied",
    pushEnabled: true,
    favoriteZoneIds: ["GWANGALLI-ZONE-CENTER"],
    plannedVisit: true,
  },
  {
    userId: "t-far",
    locationConsent: "granted",
    pushEnabled: true,
    latitude: 35.12,
    longitude: 129.08,
  },
];

const managers: ManagerUserProfile[] = [
  {
    managerId: "mgr-zone-04",
    name: "출입구 담당",
    role: "zone_manager",
    assignedZoneIds: ["GWANGALLI-ZONE-CENTER"],
    pushEnabled: true,
    onDuty: true,
  },
  {
    managerId: "mgr-supervisor",
    name: "총괄",
    role: "field_supervisor",
    assignedZoneIds: [],
    pushEnabled: true,
    onDuty: true,
  },
];

describe("알림 우선순위", () => {
  it("안전=0, 혼잡=1, 임계=2, 위험=3", () => {
    const policy = loadNotificationPolicy();
    const busy = buildNotificationInput(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.2 }),
      { previousRiskLevel: "안전" },
    );
    expect(determineNotificationPriority(busy, policy, false)).toBe(1);

    const critical = buildNotificationInput(
      baseAnalysis({
        riskLevel: "혼잡",
        adjustedDensity: 5.2,
        criticalDensityReached: true,
      }),
      { previousRiskLevel: "혼잡" },
    );
    expect(determineNotificationPriority(critical, policy, false)).toBe(2);

    const danger = buildNotificationInput(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.3 }),
      { previousRiskLevel: "혼잡" },
    );
    expect(determineNotificationPriority(danger, policy, false)).toBe(3);
  });

  it("시스템 경고는 별도 우선순위", () => {
    const policy = loadNotificationPolicy();
    const stale = buildNotificationInput(
      baseAnalysis({
        warnings: ["CCTV/측정 데이터가 120초 동안 갱신되지 않았습니다."],
      }),
      {},
    );
    stale.dataFreshness = "stale";
    expect(determineNotificationPriority(stale, policy, true)).toBe("system");
  });
});

describe("메시지 생성 — 관광객 vs 관리자", () => {
  const policy = loadNotificationPolicy();

  it("관광객 혼잡 메시지는 행동 중심", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.3 }),
      { previousRiskLevel: "안전" },
    );
    const msg = generateTouristMessage(
      input,
      tourists[0],
      1,
      policy,
    );
    expect(msg?.body).toContain("혼잡");
    expect(msg?.body).not.toContain("감지 인원");
    expect(msg?.actionHint).toBeTruthy();
  });

  it("관리자 혼잡 메시지는 수치 포함", () => {
    const input = buildNotificationInput(
      baseAnalysis({
        riskLevel: "혼잡",
        adjustedDensity: 4.3,
        detectedPeople: 430,
      }),
      { previousRiskLevel: "안전" },
    );
    const event = {
      eventId: nextEventId(),
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      previousRiskLevel: "안전",
      currentRiskLevel: "혼잡",
      adjustedDensity: 4.3,
      thresholds: input.thresholds,
      criticalDensityReached: false,
      densityTrend: "증가",
      densityIncreaseRatePerSecond: 0.05,
      confidence: 0.92,
      additionalRiskFactors: [],
      touristNotificationRequired: true,
      managerNotificationRequired: true,
      priority: 1 as const,
      status: "생성됨" as const,
      triggerReasons: [],
      isSystemWarning: false,
      isTestData: true,
      escalationLevel: 0,
      createdAt: input.measuredAt,
      updatedAt: input.measuredAt,
    };
    const msg = generateManagerMessage(input, event, 1, true);
    expect(msg.body).toContain("430");
    expect(msg.body).toContain("4.3");
    expect(msg.body).toContain("CCTV");
    expect(msg.aiReason).toBeTruthy();
  });

  it("위험 구역 내부 관광객 별도 메시지", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.5 }),
      { previousRiskLevel: "혼잡" },
    );
    const inside = generateTouristMessage(input, tourists[0], 3, policy);
    const outside = generateTouristMessage(input, tourists[1], 3, policy);
    expect(inside?.body).toContain("현재 위치가 위험 구역");
    expect(outside?.body).toContain("이동하지 말고");
  });

  it("사고 확정 표현 미사용", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.5 }),
      { previousRiskLevel: "혼잡" },
    );
    const event = {
      eventId: nextEventId(),
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      previousRiskLevel: "혼잡",
      currentRiskLevel: "위험",
      adjustedDensity: 6.5,
      thresholds: input.thresholds,
      criticalDensityReached: false,
      densityTrend: "증가",
      densityIncreaseRatePerSecond: 0.8,
      confidence: 0.92,
      additionalRiskFactors: [],
      touristNotificationRequired: true,
      managerNotificationRequired: true,
      priority: 3 as const,
      status: "생성됨" as const,
      triggerReasons: [],
      isSystemWarning: false,
      isTestData: true,
      escalationLevel: 0,
      createdAt: input.measuredAt,
      updatedAt: input.measuredAt,
    };
    const msg = generateManagerMessage(input, event, 3, true);
    expect(msg.body).not.toMatch(/사망|익사|압사 사고가 발생/);
    expect(msg.body).toContain("위험 단계");
  });
});

describe("수신 대상 선택", () => {
  it("위험 구역 내부·접근 중 사용자만", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.2 }),
      {},
    );
    const selected = selectTouristRecipients(tourists, input, 3);
    const ids = selected.map((t) => t.userId);
    expect(ids).toContain("t-in-zone");
    expect(ids).toContain("t-approaching");
    expect(ids).not.toContain("t-far");
  });

  it("위치 미동의 사용자는 관심 구역만", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.2 }),
      {},
    );
    const selected = selectTouristRecipients(tourists, input, 3);
    expect(selected.some((t) => t.userId === "t-no-location")).toBe(true);
  });

  it("혼잡은 구역 담당 관리자 우선", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "혼잡" }),
      {},
    );
    const selected = selectManagerRecipients(managers, input, 1, 0);
    expect(selected.some((m) => m.managerId === "mgr-zone-04")).toBe(true);
    expect(selected.some((m) => m.managerId === "mgr-supervisor")).toBe(false);
  });

  it("위험은 확대 대상 포함", () => {
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.5 }),
      {},
    );
    const selected = selectManagerRecipients(managers, input, 3, 0);
    expect(selected.length).toBeGreaterThan(1);
  });
});

describe("중복 발송 방지", () => {
  it("동일 등급 쿨다운 내 재발송 억제", () => {
    const policy = loadNotificationPolicy();
    policy.sameLevelCooldownSeconds = 300;
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.1 }),
      { previousRiskLevel: "혼잡" },
    );
    const existing = [
      {
        eventId: "ALERT-1",
        zoneId: input.zoneId,
        zoneName: input.zoneName,
        previousRiskLevel: "안전" as RiskLevel,
        currentRiskLevel: "혼잡" as RiskLevel,
        adjustedDensity: 4.0,
        thresholds: input.thresholds,
        criticalDensityReached: false,
        densityTrend: "증가",
        densityIncreaseRatePerSecond: 0,
        confidence: 0.9,
        additionalRiskFactors: [],
        touristNotificationRequired: true,
        managerNotificationRequired: true,
        priority: 1 as const,
        status: "발송됨" as const,
        triggerReasons: [],
        isSystemWarning: false,
        isTestData: true,
        escalationLevel: 0,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      },
    ];
    const dup = preventDuplicateNotification(
      input,
      1,
      [],
      existing,
      policy,
    );
    expect(dup.allowResend).toBe(false);
  });
});

describe("NotificationService 상태 전이", () => {
  let service: NotificationService;

  beforeEach(() => {
    service = new NotificationService({ tourists, managers });
  });

  it("안전 유지 시 알림 없음", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({
        riskLevel: "안전",
        adjustedDensity: 2,
        detectedPeople: 200,
      }),
    );
    expect(r.evaluation.shouldNotify).toBe(false);
    expect(r.touristDeliveries).toHaveLength(0);
  });

  it("안전→혼잡 상승 시 관광객·관리자 발송", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.2 }),
    );
    expect(r.evaluation.shouldNotify).toBe(true);
    expect(r.touristMessages.length).toBeGreaterThan(0);
    expect(r.managerMessages.length).toBe(1);
    expect(r.managerDeliveries.some((d) => d.success)).toBe(true);
  });

  it("임계 밀도 도달 우선순위 2", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({
        riskLevel: "혼잡",
        adjustedDensity: 5.2,
        criticalDensityReached: true,
      }),
    );
    expect(r.evaluation.priority).toBe(2);
    expect(r.managerMessages[0].title).toContain("임계 밀도");
  });

  it("혼잡→위험 최우선 경보", async () => {
    await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.5 }),
    );
    const r = await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.3 }),
    );
    expect(r.evaluation.priority).toBe(3);
    expect(r.touristMessages.some((m) => m.soundAndVibration)).toBe(true);
  });

  it("위험→혼잡 완화 — 즉시 해제 아님", async () => {
    await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.5 }),
    );
    const r = await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 5.5 }),
    );
    expect(r.managerMessages[0].title).toContain("완화");
    expect(
      r.touristMessages.some((m) => m.title.includes("해제")),
    ).toBe(false);
  });

  it("혼잡→안전 해제 (최소 지속 시간 충족)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-17T10:03:00.000Z"));
    const svc = new NotificationService({ tourists, managers });
    await svc.processAnalysisResult(
      baseAnalysis({
        riskLevel: "혼잡",
        adjustedDensity: 4.5,
        measuredAt: "2026-07-17T10:00:00.000Z",
      }),
      { nowIso: "2026-07-17T10:00:00.000Z" },
    );
    await svc.processAnalysisResult(
      baseAnalysis({
        riskLevel: "안전",
        adjustedDensity: 3.5,
        measuredAt: "2026-07-17T10:01:00.000Z",
      }),
      { nowIso: "2026-07-17T10:01:00.000Z" },
    );
    const r = await svc.processAnalysisResult(
      baseAnalysis({
        riskLevel: "안전",
        adjustedDensity: 3.4,
        measuredAt: "2026-07-17T10:03:00.000Z",
      }),
      { nowIso: "2026-07-17T10:03:00.000Z" },
    );
    expect(
      r.evaluation.triggerReasons.some((t) => t.code === "LEVEL_DOWN_TO_SAFE"),
    ).toBe(true);
    expect(r.touristMessages.some((m) => m.body.includes("안전 단계"))).toBe(
      true,
    );
    vi.useRealTimers();
  });

  it("낮은 신뢰도 → 시스템 경고", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({
        riskLevel: "혼잡",
        lowConfidence: true,
        confidence: 0.4,
      }),
    );
    expect(r.evaluation.isSystemWarning).toBe(true);
    expect(r.managerMessages[0].title).toContain("시스템 경고");
  });

  it("CCTV 데이터 stale → 시스템 경고", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({
        warnings: ["CCTV/측정 데이터가 90초 동안 갱신되지 않았습니다."],
      }),
    );
    expect(r.evaluation.isSystemWarning).toBe(true);
  });

  it("밀도 안전 + 추가 위험 요인", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({
        riskLevel: "안전",
        adjustedDensity: 2,
        densityBasedSafeButOtherRisks: true,
        auxiliaryAlerts: [
          {
            code: "OPPOSING_FLOW",
            message: "서로 반대 방향 이동",
            severity: "critical",
          },
        ],
      }),
    );
    expect(r.evaluation.shouldNotify).toBe(true);
    expect(r.touristMessages[0].body).toContain("주의");
  });

  it("중복 입력 억제", async () => {
    const first = await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.1 }),
    );
    const second = await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.15 }),
    );
    expect(first.evaluation.shouldNotify).toBe(true);
    expect(second.evaluation.isDuplicate).toBe(true);
    expect(second.evaluation.shouldNotify).toBe(false);
  });

  it("관리자 확인 기록", async () => {
    const r = await service.processAnalysisResult(
      baseAnalysis({ riskLevel: "위험", adjustedDensity: 6.5 }),
    );
    const eventId = r.event!.eventId;
    const ack = service.acknowledge(eventId, "mgr-zone-04");
    expect(ack?.status).toBe("관리자 확인");
    expect(ack?.acknowledgedBy).toBe("mgr-zone-04");
  });

  it("관리자 미응답 에스컬레이션", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-17T10:00:00.000Z"));
    const svc = new NotificationService({ tourists, managers });

    const r = await svc.processAnalysisResult(
      baseAnalysis({
        riskLevel: "위험",
        adjustedDensity: 6.8,
        measuredAt: "2026-07-17T10:00:00.000Z",
      }),
    );
    vi.advanceTimersByTime(121_000);
    const escalations = await svc.checkEscalations(r.analysisInput);
    expect(escalations.length).toBeGreaterThan(0);
    expect(escalations[0].escalationLevel).toBe(1);
    vi.useRealTimers();
  });

  it("푸시 실패 기록", async () => {
    const svc = new NotificationService({
      tourists: [
        {
          userId: "tourist-005-no-push",
          locationConsent: "granted",
          pushEnabled: false,
          currentZoneId: "GWANGALLI-ZONE-CENTER",
        },
      ],
      managers,
    });
    const r = await svc.processAnalysisResult(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.5 }),
    );
    const pushFails = r.touristDeliveries.filter(
      (d) => d.channel === "push" && !d.success,
    );
    expect(pushFails.length).toBeGreaterThan(0);
  });

  it("동일 구역 위험 알림 이벤트 생성", async () => {
    const r1 = await service.processAnalysisResult(
      baseAnalysis({
        zoneId: "GWANGALLI-ZONE-CENTER",
        riskLevel: "위험",
        adjustedDensity: 6.5,
      }),
    );
    expect(r1.event?.zoneId).toBe("GWANGALLI-ZONE-CENTER");
    expect(service.listEvents().length).toBeGreaterThanOrEqual(1);
  });
});

describe("evaluateNotificationEvent", () => {
  it("급상승 트리거", () => {
    const policy = loadNotificationPolicy();
    const input = buildNotificationInput(
      baseAnalysis({ riskLevel: "혼잡", adjustedDensity: 4.8 }),
      {
        previousRiskLevel: "안전",
        densityIncreaseRatePerSecond: 0.2,
      },
    );
    const ev = evaluateNotificationEvent(input, policy, []);
    expect(ev.shouldNotify).toBe(true);
    expect(ev.triggerReasons.some((t) => t.code === "RAPID_RISE")).toBe(true);
  });
});

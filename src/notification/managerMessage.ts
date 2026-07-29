/**
 * 관리자용 메시지 생성기
 */

import type {
  AlertEvent,
  ManagerNotificationMessage,
  NotificationAnalysisInput,
  NotificationPriority,
} from "./types.js";

function pct(confidence: number | null): string {
  if (confidence === null) return "알 수 없음";
  return `${Math.round(confidence * 100)}%`;
}

function trendLabel(trend: string, rate: number): string {
  if (rate >= 0.1) return `빠른 증가 (${rate.toFixed(2)}명/㎡·초)`;
  if (trend === "증가") return `증가 (${rate.toFixed(2)}명/㎡·초)`;
  if (trend === "감소") return `감소 (${rate.toFixed(2)}명/㎡·초)`;
  if (trend === "유지") return "유지";
  return "알 수 없음";
}

function criticalGap(input: NotificationAnalysisInput): string {
  const d = input.adjustedDensity ?? 0;
  const gap = input.thresholds.criticalDensity - d;
  if (gap > 0) return `임계 밀도까지 ${gap.toFixed(1)}명/㎡ 남음`;
  return `임계 밀도 초과 ${Math.abs(gap).toFixed(1)}명/㎡`;
}

export function generateManagerMessage(
  input: NotificationAnalysisInput,
  event: AlertEvent,
  priority: NotificationPriority,
  touristAlertSent: boolean,
): ManagerNotificationMessage {
  const density = input.adjustedDensity ?? 0;
  const trend = trendLabel(input.densityTrend, input.densityIncreaseRatePerSecond);

  if (priority === "system") {
    const staleSec = input.warnings.find((w) => w.includes("갱신되지 않"));
    return {
      audience: "manager",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: input.currentRiskLevel,
      title: "[시스템 경고]",
      body: `[시스템 경고] ${input.zoneName}의 데이터 신뢰성이 낮습니다. ${
        staleSec ?? "분석 신뢰도 저하 또는 데이터 오류"
      }. 현재 위험 등급(${input.currentRiskLevel})의 신뢰성이 낮으므로 현장 확인이 필요합니다.`,
      detectedPeople: input.detectedPeople,
      effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
      rawDensity: input.rawDensity,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: input.criticalDensityReached,
      trend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      aiReason: input.reason,
      recommendedActions: [
        "CCTV 연결 상태 확인",
        "현장 안전요원 확인",
        "수동 인원 파악",
      ],
      cctvLink: `/admin.html?zone=${input.zoneId}&cctv=1`,
      touristAlertSent: false,
      channels: ["admin_dashboard", "admin_sound", "system"],
      soundAndVibration: true,
      priority: "system",
      actions: {
        acknowledge: true,
        startResponse: true,
        resolve: true,
        falseAlarm: true,
      },
    };
  }

  if (
    input.currentRiskLevel === "안전" &&
    (input.previousRiskLevel === "혼잡" ||
      input.previousRiskLevel === "위험")
  ) {
    return {
      audience: "manager",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "안전",
      title: "[경보 해제]",
      body: `[경보 해제] ${input.zoneName}이 안전 단계로 전환되었습니다. 현재 보정 밀도 ${density.toFixed(1)}명/㎡, ${input.densityTrend} 추세입니다.`,
      detectedPeople: input.detectedPeople,
      effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
      rawDensity: input.rawDensity,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: false,
      trend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      aiReason: input.reason,
      recommendedActions: ["이벤트 종료 처리", "관광객 해제 메시지 확인"],
      cctvLink: `/admin.html?zone=${input.zoneId}`,
      touristAlertSent,
      channels: ["admin_dashboard"],
      soundAndVibration: false,
      priority: 0,
      actions: {
        acknowledge: true,
        startResponse: true,
        resolve: true,
        falseAlarm: true,
      },
    };
  }

  if (input.currentRiskLevel === "위험") {
    return {
      audience: "manager",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "위험",
      title: priority === 4 ? "[즉각 복합 위험]" : "[긴급 위험]",
      body: `[긴급 위험] ${input.zoneName}이 위험 단계에 진입했습니다. 보정 밀도 ${density.toFixed(1)}명/㎡, 최근 ${input.currentLevelDurationSeconds}초간 ${trend}. 분석 신뢰도는 ${pct(input.confidence)}입니다. CCTV와 현장을 즉시 확인하고 필요 시 관광객 이동 통제 및 구조 인력 배치를 검토해 주세요. (AI 판단만으로 자동 통제하지 않음)`,
      detectedPeople: input.detectedPeople,
      effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
      rawDensity: input.rawDensity,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: input.criticalDensityReached,
      trend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      aiReason: input.reason,
      recommendedActions: [
        "CCTV 즉시 확인",
        "현장 안전요원 파견 검토",
        "관광객 우회·이동 안내",
        "구조 인력 배치 검토 (관리자 최종 결정)",
        "인접 구역 모니터링 강화",
      ],
      cctvLink: `/admin.html?zone=${input.zoneId}&cctv=1`,
      touristAlertSent,
      channels: ["admin_dashboard", "admin_sound", "push"],
      soundAndVibration: true,
      priority: priority === 4 ? 4 : 3,
      actions: {
        acknowledge: true,
        startResponse: true,
        resolve: true,
        falseAlarm: true,
      },
    };
  }

  if (input.criticalDensityReached) {
    return {
      audience: "manager",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: input.currentRiskLevel,
      title: "[임계 밀도 도달]",
      body: `[임계 밀도 도달] ${input.zoneName}의 보정 밀도가 ${density.toFixed(1)}명/㎡로 임계값(${input.thresholds.criticalDensity})을 초과했습니다. 위험 단계 진입 가능성이 있으므로 CCTV와 현장 상황을 즉시 확인해 주세요.`,
      detectedPeople: input.detectedPeople,
      effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
      rawDensity: input.rawDensity,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: true,
      trend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      aiReason: input.reason,
      recommendedActions: [
        "CCTV 확인",
        "데이터 갱신 주기 단축",
        "관광객 우회 안내 검토",
      ],
      cctvLink: `/admin.html?zone=${input.zoneId}&cctv=1`,
      touristAlertSent,
      channels: ["admin_dashboard", "admin_sound", "push"],
      soundAndVibration: true,
      priority: 2,
      actions: {
        acknowledge: true,
        startResponse: true,
        resolve: true,
        falseAlarm: true,
      },
    };
  }

  if (input.previousRiskLevel === "위험" && input.currentRiskLevel === "혼잡") {
    return {
      audience: "manager",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "혼잡",
      title: "[위험 완화]",
      body: `[위험 완화] ${input.zoneName}이 위험에서 혼잡 단계로 완화되었습니다. 보정 밀도 ${density.toFixed(1)}명/㎡, ${trend}. 지속 모니터링이 필요합니다.`,
      detectedPeople: input.detectedPeople,
      effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
      rawDensity: input.rawDensity,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: input.criticalDensityReached,
      trend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      aiReason: input.reason,
      recommendedActions: ["현장 상황 재확인", "관광객 안내 유지"],
      cctvLink: `/admin.html?zone=${input.zoneId}`,
      touristAlertSent,
      channels: ["admin_dashboard"],
      soundAndVibration: false,
      priority: 1,
      actions: {
        acknowledge: true,
        startResponse: true,
        resolve: true,
        falseAlarm: true,
      },
    };
  }

  if (input.currentRiskLevel === "혼잡") {
    return {
      audience: "manager",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "혼잡",
      title: "[혼잡 주의]",
      body: `[혼잡 주의] ${input.zoneName}에서 혼잡이 감지되었습니다. 감지 인원 ${input.detectedPeople ?? "-"}명, 유효 면적 ${input.effectiveAreaSquareMeters}㎡, 보정 밀도 ${density.toFixed(1)}명/㎡입니다. ${criticalGap(input)}. 최근 추세: ${trend}. 분석 신뢰도 ${pct(input.confidence)}. CCTV 확인이 필요합니다.`,
      detectedPeople: input.detectedPeople,
      effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
      rawDensity: input.rawDensity,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: input.criticalDensityReached,
      trend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      aiReason: input.reason,
      recommendedActions: ["CCTV 확인", "현장 순찰", "혼잡 안내 방송 검토"],
      cctvLink: `/admin.html?zone=${input.zoneId}`,
      touristAlertSent,
      channels: ["admin_dashboard", "push"],
      soundAndVibration: false,
      priority: 1,
      actions: {
        acknowledge: true,
        startResponse: true,
        resolve: true,
        falseAlarm: true,
      },
    };
  }

  return {
    audience: "manager",
    zoneId: input.zoneId,
    zoneName: input.zoneName,
    riskLevel: input.currentRiskLevel,
    title: "[상황 안내]",
    body: `${input.zoneName} 상황 업데이트. 등급 ${input.currentRiskLevel}, 보정 밀도 ${density.toFixed(1)}명/㎡.`,
    detectedPeople: input.detectedPeople,
    effectiveAreaSquareMeters: input.effectiveAreaSquareMeters,
    rawDensity: input.rawDensity,
    adjustedDensity: input.adjustedDensity,
    thresholds: input.thresholds,
    criticalDensityReached: input.criticalDensityReached,
    trend,
    densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
    confidence: input.confidence,
    additionalRiskFactors: input.additionalRiskFactors,
    aiReason: input.reason,
    recommendedActions: ["대시보드 확인"],
    cctvLink: `/admin.html?zone=${input.zoneId}`,
    touristAlertSent,
    channels: ["admin_dashboard"],
    soundAndVibration: false,
    priority: 0,
    actions: {
      acknowledge: true,
      startResponse: true,
      resolve: true,
      falseAlarm: true,
    },
  };
}

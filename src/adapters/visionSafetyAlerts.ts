/**
 * 비전 안전지도 위험 격자 경고 메시지
 * Python safety_map.py 의 MSG_TOURIST / MSG_MANAGER 와 동일 문구를 유지합니다.
 */

export const VISION_TOURIST_DANGER_MESSAGE =
  "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요.";

export const VISION_MANAGER_DANGER_MESSAGE =
  "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요.";

export interface VisionSafetyAlerts {
  hasDanger: boolean;
  dangerCellCount: number;
  touristMessage: string | null;
  managerMessage: string | null;
}

export function normalizeVisionAlerts(
  raw: unknown,
): VisionSafetyAlerts {
  const obj = (raw ?? {}) as Record<string, unknown>;
  const hasDanger = Boolean(obj.hasDanger);
  const dangerCellCount = Number(obj.dangerCellCount ?? 0);
  if (!hasDanger || dangerCellCount <= 0) {
    return {
      hasDanger: false,
      dangerCellCount: 0,
      touristMessage: null,
      managerMessage: null,
    };
  }
  return {
    hasDanger: true,
    dangerCellCount,
    touristMessage:
      typeof obj.touristMessage === "string"
        ? obj.touristMessage
        : VISION_TOURIST_DANGER_MESSAGE,
    managerMessage:
      typeof obj.managerMessage === "string"
        ? obj.managerMessage
        : VISION_MANAGER_DANGER_MESSAGE,
  };
}

/** 격자 명/m² 배열에서 위험(>=6) 칸이 있으면 경고 객체 생성 */
export function alertsFromDensityGrid(
  grid: Array<Array<number | null>>,
  dangerThreshold = 6,
): VisionSafetyAlerts {
  let dangerCellCount = 0;
  for (const row of grid) {
    for (const v of row) {
      if (typeof v === "number" && Number.isFinite(v) && v >= dangerThreshold) {
        dangerCellCount += 1;
      }
    }
  }
  if (dangerCellCount <= 0) {
    return {
      hasDanger: false,
      dangerCellCount: 0,
      touristMessage: null,
      managerMessage: null,
    };
  }
  return {
    hasDanger: true,
    dangerCellCount,
    touristMessage: VISION_TOURIST_DANGER_MESSAGE,
    managerMessage: VISION_MANAGER_DANGER_MESSAGE,
  };
}

import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { NotificationPolicy } from "./types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = join(__dirname, "..", "..", "config", "notification.default.json");

export const DEFAULT_NOTIFICATION_POLICY: NotificationPolicy = {
  sameLevelCooldownSeconds: 300,
  minLevelDurationSeconds: 120,
  dangerRepeatIntervalSeconds: 180,
  managerAckTimeoutSeconds: 120,
  downgradeConfirmSeconds: 120,
  minDensityChangeForResend: 0.5,
  maxRetryAttempts: 3,
  retryIntervalSeconds: 30,
  touristProximityRadiusMeters: 200,
  adjacentZoneRadiusMeters: 150,
  rapidRisePerSecond: 0.1,
  emergencyDensityMultiplier: 1.25,
  staleDataSeconds: 60,
  minimumConfidence: 0.7,
  disclaimer:
    "알림은 AI 분석 참고 정보이며 사고 발생 사실을 단정하지 않습니다.",
};

export function loadNotificationPolicy(): NotificationPolicy {
  if (!existsSync(CONFIG_PATH)) return DEFAULT_NOTIFICATION_POLICY;
  const raw = JSON.parse(readFileSync(CONFIG_PATH, "utf-8")) as NotificationPolicy;
  return { ...DEFAULT_NOTIFICATION_POLICY, ...raw };
}

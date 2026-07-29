import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type {
  DensityThresholds,
  ThresholdChangeRecord,
} from "../types/index.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..");
const DEFAULT_PATH = join(ROOT, "config", "thresholds.default.json");
const RUNTIME_PATH = join(ROOT, "config", "thresholds.runtime.json");
const AUDIT_PATH = join(ROOT, "config", "threshold-changes.json");

export const DEFAULT_THRESHOLDS: DensityThresholds = {
  congestionStartDensity: 4.0,
  criticalDensity: 5.0,
  highRiskDensity: 6.0,
  measurementWindowSeconds: 30,
  minimumConfidence: 0.7,
  hysteresisMargin: 0.2,
  minDurationSecondsForUpgrade: 10,
  minDurationSecondsForDowngrade: 15,
  immediateHighRiskMultiplier: 1.25,
  rapidRisePerSecond: 0.15,
  staleDataSeconds: 120,
  disclaimer:
    "본 임계값은 현장 검증 전 초기 참고값이며, 법적·공식적 절대 기준이 아닙니다.",
};

export function validateThresholdOrder(
  thresholds: Pick<
    DensityThresholds,
    "congestionStartDensity" | "criticalDensity" | "highRiskDensity"
  >,
): { valid: boolean; message?: string } {
  const { congestionStartDensity, criticalDensity, highRiskDensity } =
    thresholds;

  if (
    !(
      Number.isFinite(congestionStartDensity) &&
      Number.isFinite(criticalDensity) &&
      Number.isFinite(highRiskDensity)
    )
  ) {
    return {
      valid: false,
      message: "임계값은 유한한 숫자여야 합니다.",
    };
  }

  if (
    !(
      congestionStartDensity > 0 &&
      criticalDensity > 0 &&
      highRiskDensity > 0
    )
  ) {
    return {
      valid: false,
      message: "임계값은 0보다 커야 합니다.",
    };
  }

  if (
    !(
      congestionStartDensity < criticalDensity &&
      criticalDensity < highRiskDensity
    )
  ) {
    return {
      valid: false,
      message:
        "임계값 순서 오류: congestionStartDensity < criticalDensity < highRiskDensity 관계를 만족해야 합니다.",
    };
  }

  return { valid: true };
}

function loadJsonFile<T>(path: string): T | null {
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

export function loadThresholds(): DensityThresholds {
  const runtime = loadJsonFile<DensityThresholds>(RUNTIME_PATH);
  const defaults = loadJsonFile<DensityThresholds>(DEFAULT_PATH);
  const merged: DensityThresholds = {
    ...DEFAULT_THRESHOLDS,
    ...(defaults ?? {}),
    ...(runtime ?? {}),
  };
  const check = validateThresholdOrder(merged);
  if (!check.valid) {
    throw new Error(
      `저장된 임계값 설정이 유효하지 않습니다: ${check.message}`,
    );
  }
  return merged;
}

export function saveThresholds(
  nextPartial: Partial<DensityThresholds>,
  meta: {
    changedBy: string;
    reason: string;
    targetZoneIds?: string[] | "all";
    fieldVerified?: boolean;
  },
): { thresholds: DensityThresholds; record: ThresholdChangeRecord } {
  const current = loadThresholds();
  const next: DensityThresholds = { ...current, ...nextPartial };
  const check = validateThresholdOrder(next);
  if (!check.valid) {
    throw new Error(check.message);
  }

  const before: Partial<DensityThresholds> = {};
  const after: Partial<DensityThresholds> = {};
  for (const key of Object.keys(nextPartial) as (keyof DensityThresholds)[]) {
    before[key] = current[key] as never;
    after[key] = next[key] as never;
  }

  mkdirSync(dirname(RUNTIME_PATH), { recursive: true });
  writeFileSync(RUNTIME_PATH, JSON.stringify(next, null, 2), "utf-8");

  const record: ThresholdChangeRecord = {
    id: `THR-${Date.now()}`,
    changedAt: new Date().toISOString(),
    changedBy: meta.changedBy,
    reason: meta.reason,
    targetZoneIds: meta.targetZoneIds ?? "all",
    fieldVerified: meta.fieldVerified ?? false,
    before,
    after,
  };

  const history = loadJsonFile<ThresholdChangeRecord[]>(AUDIT_PATH) ?? [];
  history.push(record);
  writeFileSync(AUDIT_PATH, JSON.stringify(history, null, 2), "utf-8");

  return { thresholds: next, record };
}

export function listThresholdChanges(): ThresholdChangeRecord[] {
  return loadJsonFile<ThresholdChangeRecord[]>(AUDIT_PATH) ?? [];
}

export function resolveZoneThresholds(
  global: DensityThresholds,
  overrides?: Partial<DensityThresholds>,
): { thresholds: DensityThresholds; source: "global" | "zone_override" } {
  if (!overrides || Object.keys(overrides).length === 0) {
    return { thresholds: global, source: "global" };
  }
  const merged = { ...global, ...overrides };
  const check = validateThresholdOrder(merged);
  if (!check.valid) {
    throw new Error(`구역 임계값 오버라이드 오류: ${check.message}`);
  }
  return { thresholds: merged, source: "zone_override" };
}

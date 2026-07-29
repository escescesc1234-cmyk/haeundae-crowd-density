import type { DensityInput, DataSource } from "../types/index.js";

export interface ManualMeasurementPayload {
  zoneId: string;
  effectiveAreaSquareMeters?: number;
  detectedPeople: number;
  measuredAt?: string;
  confidence?: number;
  dataSource?: Extract<DataSource, "manual" | "test" | "manager_override">;
  isTestData?: boolean;
}

export function toManualDensityInput(
  payload: ManualMeasurementPayload,
): DensityInput {
  return {
    zoneId: payload.zoneId,
    effectiveAreaSquareMeters: payload.effectiveAreaSquareMeters,
    detectedPeople: payload.detectedPeople,
    measuredAt: payload.measuredAt ?? new Date().toISOString(),
    confidence: payload.confidence ?? 1,
    dataSource: payload.dataSource ?? "manual",
    isTestData: payload.isTestData ?? payload.dataSource === "test",
  };
}

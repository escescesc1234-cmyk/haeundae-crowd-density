/**
 * 밀도 계산: 인구 밀도 = 인원수 ÷ 유효 면적(㎡)
 */

export interface DensityCalculationOk {
  ok: true;
  rawDensity: number;
  effectiveAreaSquareMeters: number;
  detectedPeople: number;
}

export interface DensityCalculationErr {
  ok: false;
  code: "INVALID_AREA" | "MISSING_PEOPLE" | "INVALID_PEOPLE";
  message: string;
}

export type DensityCalculationResult =
  | DensityCalculationOk
  | DensityCalculationErr;

export function calculateRawDensity(
  detectedPeople: number | null | undefined,
  effectiveAreaSquareMeters: number | null | undefined,
): DensityCalculationResult {
  if (
    effectiveAreaSquareMeters === null ||
    effectiveAreaSquareMeters === undefined ||
    !Number.isFinite(effectiveAreaSquareMeters) ||
    effectiveAreaSquareMeters <= 0
  ) {
    return {
      ok: false,
      code: "INVALID_AREA",
      message:
        "유효 면적이 0이거나 누락되어 밀도를 계산할 수 없습니다. 면적은 0보다 커야 합니다.",
    };
  }

  if (detectedPeople === null || detectedPeople === undefined) {
    return {
      ok: false,
      code: "MISSING_PEOPLE",
      message: "인원수가 누락되어 데이터 없음 상태입니다.",
    };
  }

  if (
    typeof detectedPeople !== "number" ||
    !Number.isFinite(detectedPeople) ||
    !Number.isInteger(detectedPeople) ||
    detectedPeople < 0
  ) {
    return {
      ok: false,
      code: "INVALID_PEOPLE",
      message: "인원수는 0 이상의 정수여야 합니다.",
    };
  }

  const rawDensity = detectedPeople / effectiveAreaSquareMeters;
  return {
    ok: true,
    rawDensity: roundDensity(rawDensity),
    effectiveAreaSquareMeters,
    detectedPeople,
  };
}

export function roundDensity(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

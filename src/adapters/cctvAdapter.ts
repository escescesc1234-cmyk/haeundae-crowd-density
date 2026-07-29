/**
 * CCTV 객체 감지 시스템 연동 어댑터 (향후 확장용)
 * 얼굴 인식/개인 신원 매핑은 수행하지 않음. trackId는 익명 추적 식별자일 뿐이다.
 */

import type {
  BoundaryDetection,
  CameraDetection,
  DensityInput,
} from "../types/index.js";
import { deduplicateCameraDetections } from "../density/deduplication.js";

export interface CctvFramePayload {
  zoneId: string;
  effectiveAreaSquareMeters?: number;
  measuredAt: string;
  cameras: CameraDetection[];
  boundaryAssignments?: BoundaryDetection[];
  isTestData?: boolean;
}

export function toCctvDensityInput(payload: CctvFramePayload): DensityInput {
  const dedup = deduplicateCameraDetections(
    payload.cameras,
    payload.boundaryAssignments ?? [],
    payload.zoneId,
  );

  return {
    zoneId: payload.zoneId,
    effectiveAreaSquareMeters: payload.effectiveAreaSquareMeters,
    detectedPeople: dedup.detectedPeople,
    measuredAt: payload.measuredAt,
    confidence: dedup.confidence,
    dataSource: "cctv_detection",
    cameraDetections: payload.cameras,
    boundaryDetections: payload.boundaryAssignments,
    isTestData: payload.isTestData ?? false,
  };
}

/**
 * 실제 CCTV 파이프라인 연결 시 구현할 인터페이스 스케치
 */
export interface CctvIngestClient {
  /** 구역별 최신 프레임 감지 결과 조회 */
  fetchLatestDetections(zoneId: string): Promise<CctvFramePayload>;
}

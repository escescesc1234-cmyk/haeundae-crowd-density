/**
 * 다중 카메라 중복 감지 및 구역 경계 중복 계산 완화
 *
 * 방안:
 * 1) 동일 trackId가 여러 카메라에 나타나면 한 번만 카운트
 * 2) 경계에 걸친 객체는 assignedZoneId(주 소속 구역)만 인정
 * 3) trackId가 없으면 카메라별 인원 합의 평균·최대값 휴리스틱(보수적: max 사용 후 경고)
 */

import type {
  BoundaryDetection,
  CameraDetection,
} from "../types/index.js";

export interface DedupResult {
  detectedPeople: number;
  confidence: number;
  warnings: string[];
  method: string;
}

export function deduplicateCameraDetections(
  detections: CameraDetection[],
  boundaryAssignments: BoundaryDetection[] = [],
  targetZoneId?: string,
): DedupResult {
  const warnings: string[] = [];

  if (!detections.length) {
    return {
      detectedPeople: 0,
      confidence: 0,
      warnings: ["카메라 감지 결과가 비어 있습니다."],
      method: "empty",
    };
  }

  const boundaryMap = new Map(
    boundaryAssignments.map((b) => [b.trackId, b.assignedZoneId]),
  );

  const allTrackIds = new Set<string>();
  let hasAnyTracks = false;

  for (const det of detections) {
    if (det.trackedObjectIds && det.trackedObjectIds.length > 0) {
      hasAnyTracks = true;
      for (const id of det.trackedObjectIds) {
        if (targetZoneId && boundaryMap.has(id)) {
          if (boundaryMap.get(id) === targetZoneId) {
            allTrackIds.add(id);
          }
        } else {
          allTrackIds.add(id);
        }
      }
    }
  }

  if (hasAnyTracks) {
    const conf =
      detections.reduce((s, d) => s + d.confidence, 0) / detections.length;
    warnings.push(
      "동일 trackId 기준 중복 제거 및 경계 주 소속 구역 할당을 적용했습니다.",
    );
    return {
      detectedPeople: allTrackIds.size,
      confidence: conf,
      warnings,
      method: "track_id_union",
    };
  }

  // trackId 없을 때: 카메라 간 단순 합산은 과다 집계 → max 사용 + 경고
  const counts = detections.map((d) => d.detectedPeople);
  const maxCount = Math.max(...counts);
  const sumCount = counts.reduce((a, b) => a + b, 0);
  const conf =
    detections.reduce((s, d) => s + d.confidence, 0) / detections.length;

  if (detections.length > 1 && sumCount > maxCount) {
    warnings.push(
      `여러 카메라에서 중복 감지 가능성: 합계 ${sumCount}명, 보수적으로 최대값 ${maxCount}명을 사용합니다. trackId 연동을 권장합니다.`,
    );
  }

  return {
    detectedPeople: maxCount,
    confidence: conf,
    warnings,
    method: "camera_max_heuristic",
  };
}

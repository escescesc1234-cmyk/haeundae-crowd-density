/**
 * YOLOv8 + SAHI 비전 파이프라인 ↔ 군중 밀도 엔진 연동 어댑터
 * Python(vision/analyze_for_app.py)을 실행해 CctvFramePayload / DensityInput 으로 변환한다.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { DensityInput } from "../types/index.js";
import {
  toCctvDensityInput,
  type CctvFramePayload,
} from "./cctvAdapter.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..");
const VISION_DIR = join(ROOT, "vision");
const ANALYZE_SCRIPT = join(VISION_DIR, "analyze_for_app.py");

export interface VisionAnalyzeRequest {
  /** 이미지 절대/상대 경로 (비전 폴더·프로젝트 루트 기준 가능) */
  imagePath: string;
  zoneId?: string;
  /** 호모그래피 캘리브레이션 JSON (fieldVerified=true 일 때만 면적 반영) */
  calibrationPath?: string;
  useHomographyArea?: boolean;
  /** 기본: VISION_PYTHON 또는 python */
  pythonPath?: string;
  timeoutMs?: number;
}

export interface VisionBridgePayload extends CctvFramePayload {
  dataSource?: "vision_yolo_sahi";
  confidence?: number;
  vision?: {
    imagePath: string;
    imageStem: string;
    rawDetections: number;
    roiPersonCount: number;
    meanConfidence: number;
    inferenceSeconds: number;
    upscale: number;
    sliceSize: number;
    overlapRatio: number;
    roiAreaM2Homography: number;
    densityPerM2Homography: number;
    homographyFieldVerified: boolean;
    calibrationPath: string | null;
    heatmapPath: string;
    heatmapRelativePath: string;
    personCentersPixels: number[][];
    personCentersMeters: number[][];
    note: string;
  };
}

export interface VisionAnalyzeResult {
  ok: true;
  payload: VisionBridgePayload;
  densityInput: DensityInput;
  raw: Record<string, unknown>;
}

function resolvePython(explicit?: string): string {
  if (explicit) return explicit;
  if (process.env.VISION_PYTHON) return process.env.VISION_PYTHON;
  // Windows에 설치한 Python 3.12 기본 경로 후보
  const win312 = join(
    process.env.LOCALAPPDATA ?? "",
    "Programs",
    "Python",
    "Python312",
    "python.exe",
  );
  if (win312 && existsSync(win312)) return win312;
  return process.platform === "win32" ? "python" : "python3";
}

function resolveImagePath(imagePath: string): string {
  if (existsSync(imagePath)) return resolve(imagePath);
  const candidates = [
    join(VISION_DIR, imagePath),
    join(ROOT, imagePath),
    join(VISION_DIR, "input", "screenshots", imagePath),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return resolve(imagePath);
}

export function runVisionAnalyze(
  req: VisionAnalyzeRequest,
): Promise<VisionAnalyzeResult> {
  const python = resolvePython(req.pythonPath);
  const imagePath = resolveImagePath(req.imagePath);
  const zoneId = req.zoneId ?? "GWANGALLI-ZONE-CENTER";
  const timeoutMs = req.timeoutMs ?? 180_000;

  if (!existsSync(ANALYZE_SCRIPT)) {
    return Promise.reject(
      new Error(`비전 스크립트가 없습니다: ${ANALYZE_SCRIPT}`),
    );
  }
  if (!existsSync(imagePath)) {
    return Promise.reject(new Error(`이미지를 찾을 수 없습니다: ${imagePath}`));
  }

  const args = [
    ANALYZE_SCRIPT,
    "--image",
    imagePath,
    "--zone-id",
    zoneId,
  ];
  if (req.calibrationPath) {
    args.push("--calibration", req.calibrationPath);
  }
  if (req.useHomographyArea) {
    args.push("--use-homography-area");
  }

  return new Promise((resolvePromise, reject) => {
    const child = spawn(python, args, {
      cwd: VISION_DIR,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`비전 분석 시간 초과 (${timeoutMs}ms)`));
    }, timeoutMs);

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf-8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf-8");
    });

    child.on("error", (err) => {
      clearTimeout(timer);
      reject(
        new Error(
          `Python 실행 실패 (${python}): ${err.message}. VISION_PYTHON 환경변수를 확인하세요.`,
        ),
      );
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      const lines = stdout
        .trim()
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean);
      const jsonLine = [...lines].reverse().find((l) => l.startsWith("{"));
      if (!jsonLine) {
        reject(
          new Error(
            `비전 JSON 응답 없음 (exit=${code}). stderr: ${stderr.slice(-800)}`,
          ),
        );
        return;
      }

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(jsonLine) as Record<string, unknown>;
      } catch (err) {
        reject(
          new Error(
            `비전 JSON 파싱 실패: ${err instanceof Error ? err.message : String(err)}`,
          ),
        );
        return;
      }

      if (parsed.ok === false) {
        reject(new Error(String(parsed.error ?? "비전 분석 실패")));
        return;
      }

      try {
        const payload = toVisionBridgePayload(parsed);
        const densityInput = toVisionDensityInput(payload);
        resolvePromise({ ok: true, payload, densityInput, raw: parsed });
      } catch (err) {
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  });
}

export function toVisionBridgePayload(
  raw: Record<string, unknown>,
): VisionBridgePayload {
  const cameras = (raw.cameras as CctvFramePayload["cameras"]) ?? [];
  return {
    zoneId: String(raw.zoneId),
    measuredAt: String(raw.measuredAt),
    detectedPeople: undefined,
    effectiveAreaSquareMeters:
      typeof raw.effectiveAreaSquareMeters === "number"
        ? raw.effectiveAreaSquareMeters
        : undefined,
    cameras:
      cameras.length > 0
        ? cameras
        : [
            {
              cameraId: "vision-sahi-yolov8",
              detectedPeople: Number(raw.detectedPeople ?? 0),
              confidence: Number(raw.confidence ?? 0),
              measuredAt: String(raw.measuredAt),
            },
          ],
    isTestData: Boolean(raw.isTestData ?? true),
    dataSource: "vision_yolo_sahi",
    confidence:
      typeof raw.confidence === "number" ? raw.confidence : undefined,
    vision: raw.vision as VisionBridgePayload["vision"],
  };
}

export function toVisionDensityInput(payload: VisionBridgePayload): DensityInput {
  const base = toCctvDensityInput(payload);
  return {
    ...base,
    dataSource: "vision_yolo_sahi",
    confidence: payload.confidence ?? base.confidence,
    auxiliaryFactors: {
      ...base.auxiliaryFactors,
      cctvAnalysisConfidence: payload.confidence ?? base.confidence,
    },
  };
}

/** 스크린샷 폴더의 기본 샘플 경로 */
export function defaultVisionScreenshot(stem = "01_wide_full_beach.png"): string {
  return join(VISION_DIR, "input", "screenshots", stem);
}

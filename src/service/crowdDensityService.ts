/**
 * 인메모리 분석 세션 — AI LAB 앱에 임베드하거나 API 서버에서 사용
 */

import {
  analyzeZoneDensity,
  createInitialZoneState,
} from "../density/engine.js";
import { loadThresholds, saveThresholds, listThresholdChanges } from "../config/thresholds.js";
import { loadZoneCatalog, getZoneById } from "../zone/zoneService.js";
import { toManualDensityInput } from "../adapters/manualAdapter.js";
import { toCctvDensityInput, type CctvFramePayload } from "../adapters/cctvAdapter.js";
import {
  runVisionAnalyze,
  type VisionAnalyzeRequest,
  type VisionAnalyzeResult,
} from "../adapters/visionAdapter.js";
import {
  createAlertDispatch,
  createFalsePositiveRecord,
} from "../views/adminView.js";
import type {
  DensityAnalysisResult,
  DensityInput,
  DensityThresholds,
  RiskLevel,
  ZoneRuntimeState,
} from "../types/index.js";
import type { NotificationService } from "../notification/service.js";
import type { ProcessNotificationResult } from "../notification/types.js";
import { computeTrend, parseIsoMs } from "../density/smoothing.js";

export class CrowdDensityService {
  private runtime = new Map<string, ZoneRuntimeState>();
  private lastResults = new Map<string, DensityAnalysisResult>();
  private alerts: unknown[] = [];
  private falsePositives: unknown[] = [];
  private catalog = loadZoneCatalog();
  private notificationService?: NotificationService;

  constructor(notificationService?: NotificationService) {
    this.notificationService = notificationService;
    for (const zone of this.catalog.zones) {
      this.runtime.set(zone.zoneId, createInitialZoneState(zone));
    }
  }

  setNotificationService(service: NotificationService) {
    this.notificationService = service;
  }

  getCatalog() {
    return this.catalog;
  }

  getThresholds(): DensityThresholds {
    return loadThresholds();
  }

  updateThresholds(
    partial: Partial<DensityThresholds>,
    meta: {
      changedBy: string;
      reason: string;
      targetZoneIds?: string[] | "all";
      fieldVerified?: boolean;
    },
  ) {
    return saveThresholds(partial, meta);
  }

  getThresholdChangeLog() {
    return listThresholdChanges();
  }

  private getRuntime(zoneId: string): ZoneRuntimeState {
    const zone = getZoneById(zoneId, this.catalog);
    let state = this.runtime.get(zoneId);
    if (!state) {
      state = createInitialZoneState(zone);
      this.runtime.set(zoneId, state);
    }
    return state;
  }

  analyze(input: DensityInput, options?: { skipHysteresis?: boolean }) {
    const zone = getZoneById(input.zoneId, this.catalog);
    const runtime = this.getRuntime(input.zoneId);
    const { result, runtime: next } = analyzeZoneDensity(
      zone,
      input,
      runtime,
      {
        globalThresholds: this.getThresholds(),
        skipHysteresis: options?.skipHysteresis,
      },
    );
    this.runtime.set(input.zoneId, next);
    this.lastResults.set(input.zoneId, result);
    return result;
  }

  async analyzeAndNotify(
    input: DensityInput,
    options?: {
      skipHysteresis?: boolean;
      manualCongestionBroadcast?: boolean;
    },
  ): Promise<{
    analysis: DensityAnalysisResult;
    notification?: ProcessNotificationResult;
  }> {
    const runtime = this.getRuntime(input.zoneId);
    const previousRiskLevel = runtime.riskLevel;
    const result = this.analyze(input, options);
    const nextRuntime = this.getRuntime(input.zoneId);

    const { ratePerSecond } = computeTrend(
      nextRuntime.densityHistory,
      result.adjustedDensity ?? 0,
      result.measuredAt,
      this.getThresholds().measurementWindowSeconds,
    );

    const durationSec = nextRuntime.currentRiskSince
      ? (parseIsoMs(result.measuredAt) - parseIsoMs(nextRuntime.currentRiskSince)) / 1000
      : 0;

    if (!this.notificationService) {
      return { analysis: result };
    }

    const notification = await this.notificationService.processAnalysisResult(
      {
        ...result,
        previousRiskLevel,
        currentLevelDurationSeconds: durationSec,
        densityIncreaseRatePerSecond: ratePerSecond,
        managerConfirmStatus: nextRuntime.managerConfirmStatus,
      },
      { manualCongestionBroadcast: options?.manualCongestionBroadcast },
    );

    return { analysis: result, notification };
  }

  analyzeManual(payload: {
    zoneId: string;
    detectedPeople: number;
    effectiveAreaSquareMeters?: number;
    measuredAt?: string;
    confidence?: number;
    isTestData?: boolean;
  }, options?: { skipHysteresis?: boolean }) {
    return this.analyze(toManualDensityInput(payload), options);
  }

  analyzeCctv(payload: CctvFramePayload, options?: { skipHysteresis?: boolean }) {
    return this.analyze(toCctvDensityInput(payload), options);
  }

  /**
   * YOLOv8+SAHI 비전 파이프라인 실행 후 밀도 엔진에 주입
   */
  async analyzeVision(
    req: VisionAnalyzeRequest,
    options?: { skipHysteresis?: boolean; notify?: boolean; manualBroadcast?: boolean },
  ): Promise<{
    vision: VisionAnalyzeResult;
    analysis: ReturnType<CrowdDensityService["analyze"]>;
    notification?: Awaited<ReturnType<CrowdDensityService["analyzeAndNotify"]>>["notification"];
  }> {
    const vision = await runVisionAnalyze(req);
    if (options?.notify) {
      const notified = await this.analyzeAndNotify(vision.densityInput, {
        skipHysteresis: options.skipHysteresis,
        manualCongestionBroadcast: options.manualBroadcast,
      });
      return {
        vision,
        analysis: notified.analysis,
        notification: notified.notification,
      };
    }
    const analysis = this.analyze(vision.densityInput, {
      skipHysteresis: options?.skipHysteresis,
    });
    return { vision, analysis };
  }

  analyzeAll(
    measurements: Array<{ zoneId: string; detectedPeople: number; measuredAt?: string }>,
    options?: { skipHysteresis?: boolean },
  ) {
    return measurements.map((m) => this.analyzeManual(m, options));
  }

  getLatestResults(): DensityAnalysisResult[] {
    return this.catalog.zones.map((z) => {
      const existing = this.lastResults.get(z.zoneId);
      if (existing) return existing;
      return this.analyze({
        zoneId: z.zoneId,
        detectedPeople: null,
        measuredAt: new Date().toISOString(),
        dataSource: "manual",
      });
    });
  }

  getRuntimeState(zoneId: string) {
    return this.getRuntime(zoneId);
  }

  manualOverride(params: {
    zoneId: string;
    riskLevel: RiskLevel;
    by: string;
    reason: string;
  }) {
    const state = this.getRuntime(params.zoneId);
    state.riskLevel = params.riskLevel;
    state.lastManualOverride = {
      riskLevel: params.riskLevel,
      by: params.by,
      at: new Date().toISOString(),
      reason: params.reason,
    };
    state.managerConfirmStatus = "overridden";
    const last = this.lastResults.get(params.zoneId);
    if (last) {
      last.riskLevel = params.riskLevel;
      last.reason = `관리자 수동 조정: ${params.reason}`;
      last.requiresManagerReview = false;
      last.touristSummary.riskLevel = params.riskLevel;
      last.adminSummary.riskLevel = params.riskLevel;
      last.adminSummary.reason = last.reason;
    }
    return state;
  }

  confirmZone(zoneId: string) {
    const state = this.getRuntime(zoneId);
    state.managerConfirmStatus = "confirmed";
    return state;
  }

  sendAlert(zoneId: string, message: string, sentBy: string) {
    const record = createAlertDispatch({ zoneId, message, sentBy });
    this.alerts.push(record);
    return record;
  }

  recordFalsePositive(
    zoneId: string,
    reportedRiskLevel: string,
    actualNote: string,
    recordedBy: string,
  ) {
    const record = createFalsePositiveRecord({
      zoneId,
      reportedRiskLevel,
      actualNote,
      recordedBy,
    });
    this.falsePositives.push(record);
    return record;
  }

  listAlerts() {
    return this.alerts;
  }

  listFalsePositives() {
    return this.falsePositives;
  }
}

import { sharedNotificationService } from "../notification/service.js";

export const sharedService = new CrowdDensityService(sharedNotificationService);

/**
 * 알림 서비스 — 이벤트 기반 오케스트레이션
 */

import type { DensityAnalysisResult, RiskLevel } from "../types/index.js";
import { buildNotificationInput } from "./adapter.js";
import { loadNotificationPolicy } from "./config.js";
import { isHighPriority } from "./priority.js";
import {
  evaluateNotificationEvent,
  shouldSendTouristNotification,
  shouldSendManagerNotification,
} from "./evaluator.js";
import { shouldSendNotification } from "./duplicate.js";
import { escalateUnacknowledgedAlert } from "./escalation.js";
import {
  acknowledgeManagerAlert,
  resolveAlertEvent,
  startManagerResponse,
} from "./escalation.js";
import { generateTouristMessage } from "./touristMessage.js";
import { generateManagerMessage } from "./managerMessage.js";
import { selectManagerRecipients, selectTouristRecipients } from "./recipients.js";
import {
  recordNotificationLog,
  retryFailedNotification,
  sendNotification,
} from "./send.js";
import {
  NotificationStore,
  nextEventId,
} from "./storage.js";
import {
  createDefaultChannels,
  type MockNotificationChannel,
} from "./channels/mockChannel.js";
import type {
  AlertEvent,
  DensityResultWithContext,
  ManagerUserProfile,
  NotificationChannelType,
  ProcessNotificationResult,
  TouristUserProfile,
  ZoneGeoHint,
} from "./types.js";
import { parseIsoMs } from "../density/smoothing.js";

export interface NotificationServiceOptions {
  tourists?: TouristUserProfile[];
  managers?: ManagerUserProfile[];
  zoneGeo?: ZoneGeoHint[];
}

export class NotificationService {
  private store = new NotificationStore();
  private channels = createDefaultChannels();
  private policy = loadNotificationPolicy();
  private tourists: TouristUserProfile[];
  private managers: ManagerUserProfile[];
  private zoneGeo: ZoneGeoHint[];
  private levelSince = new Map<string, { level: RiskLevel; since: string }>();

  constructor(options: NotificationServiceOptions = {}) {
    this.tourists = options.tourists ?? defaultTourists();
    this.managers = options.managers ?? defaultManagers();
    this.zoneGeo = options.zoneGeo ?? defaultZoneGeo();
  }

  getStore() {
    return this.store;
  }

  getChannels() {
    return this.channels;
  }

  setTourists(users: TouristUserProfile[]) {
    this.tourists = users;
  }

  setManagers(users: ManagerUserProfile[]) {
    this.managers = users;
  }

  private trackLevelDuration(zoneId: string, level: RiskLevel, measuredAt: string) {
    const prev = this.levelSince.get(zoneId);
    if (!prev || prev.level !== level) {
      this.levelSince.set(zoneId, { level, since: measuredAt });
      return 0;
    }
    return (parseIsoMs(measuredAt) - parseIsoMs(prev.since)) / 1000;
  }

  private getZoneGeo(zoneId: string): ZoneGeoHint | undefined {
    return this.zoneGeo.find((z) => z.zoneId === zoneId);
  }

  private getAdjacentZones(zoneId: string): ZoneGeoHint[] {
    const zone = this.getZoneGeo(zoneId);
    if (!zone) return [];
    return this.zoneGeo.filter((z) => zone.adjacentZoneIds.includes(z.zoneId));
  }

  async processAnalysisResult(
    result: DensityResultWithContext,
    options?: {
      manualCongestionBroadcast?: boolean;
      nowIso?: string;
    },
  ): Promise<ProcessNotificationResult> {
    const nowIso = options?.nowIso ?? new Date().toISOString();
    let previousRiskLevel =
      (this.store.getPreviousRiskLevel(result.zoneId) as RiskLevel | null) ??
      result.previousRiskLevel ??
      null;
    const existingActiveHighEvent = this.store
      .listActiveEvents(result.zoneId)
      .find((event) => event.currentRiskLevel === "혼잡" || event.currentRiskLevel === "위험");
    if (result.riskLevel === "안전" && existingActiveHighEvent) {
      previousRiskLevel = existingActiveHighEvent.currentRiskLevel;
    }

    const duration = this.trackLevelDuration(
      result.zoneId,
      result.riskLevel,
      result.measuredAt,
    );

    const input = buildNotificationInput(result, {
      previousRiskLevel,
      currentLevelDurationSeconds: duration,
      densityIncreaseRatePerSecond: result.densityIncreaseRatePerSecond ?? 0,
      nowIso,
      manualCongestionBroadcast: options?.manualCongestionBroadcast,
    });

    const activeEvents = this.store.listActiveEvents(result.zoneId);
    const evaluation = evaluateNotificationEvent(
      input,
      this.policy,
      activeEvents,
    );

    const emptyResult: ProcessNotificationResult = {
      analysisInput: input,
      evaluation,
      touristMessages: [],
      managerMessages: [],
      touristDeliveries: [],
      managerDeliveries: [],
      escalations: [],
    };

    if (!shouldSendNotification(evaluation)) {
      if (evaluation.updateExistingEventId && evaluation.isDuplicate) {
        this.store.updateEvent(evaluation.updateExistingEventId, {
          adjustedDensity: input.adjustedDensity,
          currentRiskLevel: input.currentRiskLevel,
          updatedAt: nowIso,
        });
      }
      this.store.setPreviousRiskLevel(result.zoneId, result.riskLevel);
      return emptyResult;
    }

    const event: AlertEvent = {
      eventId: nextEventId(),
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      previousRiskLevel,
      currentRiskLevel: input.currentRiskLevel,
      adjustedDensity: input.adjustedDensity,
      thresholds: input.thresholds,
      criticalDensityReached: input.criticalDensityReached,
      densityTrend: input.densityTrend,
      densityIncreaseRatePerSecond: input.densityIncreaseRatePerSecond,
      confidence: input.confidence,
      additionalRiskFactors: input.additionalRiskFactors,
      touristNotificationRequired: evaluation.touristRequired,
      managerNotificationRequired: evaluation.managerRequired,
      priority: evaluation.priority,
      status: "생성됨",
      triggerReasons: evaluation.triggerReasons,
      isSystemWarning: evaluation.isSystemWarning,
      isTestData: input.isTestData,
      escalationLevel: 0,
      createdAt: nowIso,
      updatedAt: nowIso,
    };

    this.store.saveEvent(event);

    const touristMessages = [];
    const managerMessages = [];
    const touristDeliveries = [];
    const managerDeliveries = [];

    let touristSent = false;

    if (evaluation.touristRequired && !evaluation.isSystemWarning) {
      const zone = this.getZoneGeo(input.zoneId);
      const recipients = selectTouristRecipients(
        this.tourists,
        input,
        evaluation.priority,
        zone,
        this.getAdjacentZones(input.zoneId),
      );

      for (const user of recipients) {
        const forceRelief = evaluation.triggerReasons.some(
          (t) => t.code === "LEVEL_DOWN_TO_SAFE",
        );
        const msg = generateTouristMessage(
          input,
          user,
          evaluation.priority,
          this.policy,
          { forceRelief },
        );
        if (!msg) continue;
        touristMessages.push(msg);

        const noLocationBroadcast =
          user.locationConsent !== "granted" &&
          (input.currentRiskLevel === "위험" || isHighPriority(evaluation.priority));
        const channels = noLocationBroadcast
          ? msg.channels.filter((c) => c !== "push")
          : msg.channels;
        const deliveries = await sendNotification(this.channels, {
          eventId: event.eventId,
          recipientId: user.userId,
          recipientType: "tourist",
          message: { ...msg, channels },
          pushEnabled: user.pushEnabled,
        });
        touristDeliveries.push(...deliveries);
        recordNotificationLog(this.store, deliveries);
        touristSent = deliveries.some((d) => d.success);
      }
    }

    if (evaluation.managerRequired) {
      const mgrRecipients = selectManagerRecipients(
        this.managers,
        input,
        evaluation.priority,
        0,
      );
      const mgrMsg = generateManagerMessage(
        input,
        event,
        evaluation.priority,
        touristSent,
      );
      managerMessages.push(mgrMsg);

      for (const manager of mgrRecipients) {
        const deliveries = await sendNotification(this.channels, {
          eventId: event.eventId,
          recipientId: manager.managerId,
          recipientType: "manager",
          message: mgrMsg,
          pushEnabled: manager.pushEnabled,
        });
        managerDeliveries.push(...deliveries);
        recordNotificationLog(this.store, deliveries);

        for (const failed of deliveries.filter((d) => !d.success)) {
          const retry = await retryFailedNotification(
            this.channels,
            failed,
            mgrMsg,
            this.policy,
            1,
          );
          if (retry) {
            managerDeliveries.push(retry);
            recordNotificationLog(this.store, [retry]);
          }
        }
      }
    }

    this.store.updateEvent(event.eventId, { status: "발송됨" });
    this.store.setPreviousRiskLevel(result.zoneId, result.riskLevel);

    if (
      result.riskLevel === "안전" &&
      evaluation.triggerReasons.some((t) => t.code === "LEVEL_DOWN_TO_SAFE")
    ) {
      for (const ev of this.store.listActiveEvents(result.zoneId)) {
        if (ev.currentRiskLevel === "혼잡" || ev.currentRiskLevel === "위험") {
          resolveAlertEvent(this.store, ev.eventId, "해결됨");
        }
      }
    }

    const escalations = await this.checkEscalations(input);

    return {
      analysisInput: input,
      evaluation,
      event: this.store.getEvent(event.eventId),
      touristMessages,
      managerMessages,
      touristDeliveries,
      managerDeliveries,
      escalations,
    };
  }

  async checkEscalations(
    input?: Parameters<typeof buildNotificationInput>[0] extends never ? never : import("./types.js").NotificationAnalysisInput,
  ): Promise<AlertEvent[]> {
    const escalated: AlertEvent[] = [];
    const active = this.store.listActiveEvents();

    for (const ev of active) {
      if (ev.priority !== 3 && ev.priority !== 4 && ev.priority !== "system") {
        continue;
      }
      const analysisInput =
        input ??
        ({
          zoneId: ev.zoneId,
          zoneName: ev.zoneName,
          zoneType: "sand_beach",
          detectedPeople: null,
          effectiveAreaSquareMeters: 100,
          rawDensity: null,
          adjustedDensity: ev.adjustedDensity,
          currentRiskLevel: ev.currentRiskLevel,
          previousRiskLevel: ev.previousRiskLevel,
          thresholds: ev.thresholds,
          criticalDensityReached: ev.criticalDensityReached,
          approachingHighRisk: false,
          densityTrend: ev.densityTrend,
          densityIncreaseRatePerSecond: ev.densityIncreaseRatePerSecond,
          currentLevelDurationSeconds: 0,
          confidence: ev.confidence,
          measuredAt: new Date().toISOString(),
          dataFreshness: "fresh",
          lowConfidence: false,
          additionalRiskFactors: ev.additionalRiskFactors,
          managerConfirmStatus: "unconfirmed",
          nearbyTouristCount: 0,
          reason: "",
          errors: [],
          warnings: [],
          densityBasedSafeButOtherRisks: false,
          suddenScatterOrCollapse: false,
          isTestData: ev.isTestData,
        } as import("./types.js").NotificationAnalysisInput);

      const result = await escalateUnacknowledgedAlert(
        this.store,
        this.channels,
        this.managers,
        this.policy,
        analysisInput,
        ev,
      );
      if (result) escalated.push(result);
    }
    return escalated;
  }

  acknowledge(eventId: string, managerId: string) {
    return acknowledgeManagerAlert(this.store, eventId, managerId);
  }

  startResponse(eventId: string, managerId: string) {
    return startManagerResponse(this.store, eventId, managerId);
  }

  resolve(eventId: string, status: "해결됨" | "오경보" | "자동 종료" = "해결됨") {
    return resolveAlertEvent(this.store, eventId, status);
  }

  listEvents() {
    return this.store.listAllEvents();
  }

  listDeliveries(eventId?: string) {
    return this.store.listDeliveries(eventId);
  }

  getChannelLogs(type?: NotificationChannelType) {
    if (!type) {
      return [...this.channels.values()].flatMap((c) => c.getLogs());
    }
    return this.channels.get(type)?.getLogs() ?? [];
  }
}

function defaultTourists(): TouristUserProfile[] {
  return [
    {
      userId: "tourist-001",
      locationConsent: "granted",
      pushEnabled: true,
      currentZoneId: "GWANGALLI-ZONE-CENTER",
      latitude: 35.1532,
      longitude: 129.1186,
    },
    {
      userId: "tourist-002",
      locationConsent: "granted",
      pushEnabled: true,
      headingTowardZoneId: "GWANGALLI-ZONE-CENTER",
      latitude: 35.1595,
      longitude: 129.161,
    },
    {
      userId: "tourist-003",
      locationConsent: "denied",
      pushEnabled: true,
      favoriteZoneIds: ["GWANGALLI-ZONE-CENTER"],
      plannedVisit: true,
    },
    {
      userId: "tourist-004",
      locationConsent: "granted",
      pushEnabled: false,
      currentZoneId: "GWANGALLI-ZONE-CENTER",
      insideRiskZone: true,
      latitude: 35.1598,
      longitude: 129.1615,
    },
    {
      userId: "tourist-005-no-push",
      locationConsent: "granted",
      pushEnabled: false,
      currentZoneId: "GWANGALLI-ZONE-CENTER",
      latitude: 35.1599,
      longitude: 129.1616,
    },
  ];
}

function defaultManagers(): ManagerUserProfile[] {
  return [
    {
      managerId: "mgr-center",
      name: "중앙 구역 담당",
      role: "zone_manager",
      assignedZoneIds: ["GWANGALLI-ZONE-CENTER"],
      pushEnabled: true,
      onDuty: true,
    },
    {
      managerId: "mgr-supervisor",
      name: "현장 총괄",
      role: "field_supervisor",
      assignedZoneIds: ["GWANGALLI-ZONE-CENTER"],
      pushEnabled: true,
      onDuty: true,
    },
    {
      managerId: "mgr-ops",
      name: "운영 관리자",
      role: "operations_admin",
      assignedZoneIds: [],
      pushEnabled: true,
      onDuty: true,
    },
    {
      managerId: "mgr-rescue",
      name: "구조 인력",
      role: "rescue_staff",
      assignedZoneIds: [],
      pushEnabled: true,
      onDuty: true,
    },
  ];
}

function defaultZoneGeo(): ZoneGeoHint[] {
  return [
    {
      zoneId: "GWANGALLI-ZONE-CENTER",
      latitude: 35.1532,
      longitude: 129.1186,
      adjacentZoneIds: [],
    },
  ];
}

export const sharedNotificationService = new NotificationService();

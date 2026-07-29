/**
 * 알림 이벤트·발송 기록 인메모리 저장소
 */

import type {
  AlertEvent,
  NotificationDeliveryRecord,
} from "./types.js";

let eventCounter = 0;

export function nextEventId(): string {
  eventCounter += 1;
  const year = new Date().getFullYear();
  return `ALERT-${year}-${String(eventCounter).padStart(4, "0")}`;
}

let deliveryCounter = 0;

export function nextDeliveryId(): string {
  deliveryCounter += 1;
  return `DEL-${Date.now()}-${deliveryCounter}`;
}

export class NotificationStore {
  private events = new Map<string, AlertEvent>();
  private deliveries: NotificationDeliveryRecord[] = [];
  private zoneRiskHistory = new Map<string, string>();

  getPreviousRiskLevel(zoneId: string): string | null {
    return this.zoneRiskHistory.get(zoneId) ?? null;
  }

  setPreviousRiskLevel(zoneId: string, level: string) {
    this.zoneRiskHistory.set(zoneId, level);
  }

  saveEvent(event: AlertEvent) {
    this.events.set(event.eventId, event);
  }

  getEvent(eventId: string): AlertEvent | undefined {
    return this.events.get(eventId);
  }

  updateEvent(eventId: string, patch: Partial<AlertEvent>): AlertEvent | undefined {
    const existing = this.events.get(eventId);
    if (!existing) return undefined;
    const updated = {
      ...existing,
      ...patch,
      updatedAt: new Date().toISOString(),
    };
    this.events.set(eventId, updated);
    return updated;
  }

  listActiveEvents(zoneId?: string): AlertEvent[] {
    const terminal = new Set(["해결됨", "오경보", "자동 종료"]);
    return [...this.events.values()].filter(
      (e) =>
        !terminal.has(e.status) &&
        (zoneId === undefined || e.zoneId === zoneId),
    );
  }

  listAllEvents(): AlertEvent[] {
    return [...this.events.values()].sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  recordNotificationLog(record: NotificationDeliveryRecord) {
    this.deliveries.push(record);
  }

  listDeliveries(eventId?: string): NotificationDeliveryRecord[] {
    return this.deliveries.filter(
      (d) => eventId === undefined || d.eventId === eventId,
    );
  }

  listFailedDeliveries(): NotificationDeliveryRecord[] {
    return this.deliveries.filter((d) => !d.success);
  }
}

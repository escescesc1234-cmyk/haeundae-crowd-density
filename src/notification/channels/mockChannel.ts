/**
 * 모의 알림 채널 — 실제 FCM/APNs/SMS 미연동
 */

import type { NotificationChannel, NotificationChannelType } from "../types.js";

export class MockNotificationChannel implements NotificationChannel {
  readonly type: NotificationChannelType;
  private failures = new Set<string>();
  private logs: Array<Record<string, unknown>> = [];

  constructor(type: NotificationChannelType) {
    this.type = type;
  }

  simulateFailureForRecipient(recipientId: string) {
    this.failures.add(recipientId);
  }

  clearFailures() {
    this.failures.clear();
  }

  getLogs() {
    return [...this.logs];
  }

  async send(payload: {
    recipientId: string;
    recipientType: string;
    title: string;
    body: string;
    priority: unknown;
    eventId: string;
    soundAndVibration?: boolean;
  }): Promise<{ success: boolean; failureReason?: string }> {
    const entry = {
      channel: this.type,
      ...payload,
      sentAt: new Date().toISOString(),
    };
    this.logs.push(entry);

    if (this.failures.has(payload.recipientId)) {
      return {
        success: false,
        failureReason: `모의 발송 실패 (${this.type})`,
      };
    }

    if (this.type === "push" && payload.recipientId.endsWith("-no-push")) {
      return {
        success: false,
        failureReason: "푸시 알림 권한이 꺼져 있습니다.",
      };
    }

    return { success: true };
  }
}

export function createDefaultChannels(): Map<NotificationChannelType, MockNotificationChannel> {
  const types: NotificationChannelType[] = [
    "in_app_banner",
    "push",
    "map_overlay",
    "admin_dashboard",
    "admin_sound",
    "broadcast",
    "sms",
    "system",
  ];
  const map = new Map<NotificationChannelType, MockNotificationChannel>();
  for (const t of types) {
    map.set(t, new MockNotificationChannel(t));
  }
  return map;
}

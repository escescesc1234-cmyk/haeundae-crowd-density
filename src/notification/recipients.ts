/**
 * 발송 대상 선택 — 개인정보 최소 범위
 */

import type {
  ManagerRole,
  ManagerUserProfile,
  NotificationAnalysisInput,
  NotificationPriority,
  TouristUserProfile,
  ZoneGeoHint,
} from "./types.js";
import { loadNotificationPolicy } from "./config.js";

function haversineMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) *
      Math.cos(toRad(lat2)) *
      Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function distanceToZone(
  user: TouristUserProfile,
  zone: ZoneGeoHint,
): number | null {
  if (
    user.latitude === undefined ||
    user.longitude === undefined ||
    user.locationConsent !== "granted"
  ) {
    return null;
  }
  return haversineMeters(
    user.latitude,
    user.longitude,
    zone.latitude,
    zone.longitude,
  );
}

function isHighPriority(priority: NotificationPriority): boolean {
  return priority === 3 || priority === 4;
}

export function selectTouristRecipients(
  tourists: TouristUserProfile[],
  input: NotificationAnalysisInput,
  priority: NotificationPriority,
  zoneGeo?: ZoneGeoHint,
  adjacentZones: ZoneGeoHint[] = [],
): TouristUserProfile[] {
  const policy = loadNotificationPolicy();
  const selected: TouristUserProfile[] = [];

  for (const user of tourists) {
    if (input.currentRiskLevel === "안전" && priority === 0) {
      if (
        user.currentZoneId === input.zoneId ||
        user.favoriteZoneIds?.includes(input.zoneId)
      ) {
        selected.push(user);
      }
      continue;
    }

    let match = false;

    if (user.currentZoneId === input.zoneId || user.insideRiskZone) {
      match = true;
    }

    if (user.headingTowardZoneId === input.zoneId) {
      match = true;
    }

    if (user.favoriteZoneIds?.includes(input.zoneId) || user.plannedVisit) {
      match = true;
    }

    if (zoneGeo && user.locationConsent === "granted") {
      const dist = distanceToZone(user, zoneGeo);
      if (dist !== null && dist <= policy.touristProximityRadiusMeters) {
        match = true;
      }
      for (const adj of adjacentZones) {
        const adjDist = distanceToZone(user, adj);
        if (
          adjDist !== null &&
          adjDist <= policy.adjacentZoneRadiusMeters &&
          (input.currentRiskLevel === "위험" || isHighPriority(priority))
        ) {
          match = true;
        }
      }
    }

    if (match) {
      selected.push({
        ...user,
        insideRiskZone: user.currentZoneId === input.zoneId,
      });
    }
  }

  if (
    selected.length === 0 &&
    (input.currentRiskLevel === "위험" || isHighPriority(priority))
  ) {
    return tourists.filter(
      (u) =>
        u.locationConsent !== "granted" &&
        (u.favoriteZoneIds?.includes(input.zoneId) || u.plannedVisit),
    );
  }

  return selected;
}

const ESCALATION_ROLES: ManagerRole[][] = [
  ["zone_manager"],
  ["field_supervisor", "traffic_controller"],
  ["operations_admin"],
  ["safety_officer", "rescue_staff"],
  ["system_admin"],
];

export function selectManagerRecipients(
  managers: ManagerUserProfile[],
  input: NotificationAnalysisInput,
  priority: NotificationPriority,
  escalationLevel = 0,
): ManagerUserProfile[] {
  const onDuty = managers.filter((m) => m.onDuty);
  const zoneManagers = onDuty.filter((m) =>
    m.assignedZoneIds.includes(input.zoneId),
  );

  if (isHighPriority(priority) || priority === "system") {
    const roles = ESCALATION_ROLES.slice(
      0,
      Math.min(escalationLevel + 2, ESCALATION_ROLES.length),
    ).flat();
    const escalated = onDuty.filter(
      (m) =>
        roles.includes(m.role) ||
        m.assignedZoneIds.includes(input.zoneId),
    );
    return escalated.length > 0 ? escalated : onDuty;
  }

  if (priority === 2) {
    const primary = zoneManagers.length > 0 ? zoneManagers : onDuty;
    const supervisors = onDuty.filter((m) =>
      ["field_supervisor", "operations_admin"].includes(m.role),
    );
    return [...new Map([...primary, ...supervisors].map((m) => [m.managerId, m])).values()];
  }

  if (priority === 1) {
    return zoneManagers.length > 0 ? zoneManagers : onDuty.filter((m) => m.role === "zone_manager");
  }

  if (escalationLevel > 0) {
    const roles = ESCALATION_ROLES[
      Math.min(escalationLevel, ESCALATION_ROLES.length - 1)
    ];
    return onDuty.filter((m) => roles.includes(m.role));
  }

  return zoneManagers;
}

export function getEscalationRoles(level: number): ManagerRole[] {
  return ESCALATION_ROLES[Math.min(level, ESCALATION_ROLES.length - 1)] ?? [];
}

import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ZoneDefinition } from "../types/index.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..");
const ZONES_PATH = join(ROOT, "config", "zones.gwangalli.json");

export interface BeachZoneCatalog {
  beachId: string;
  beachName: string;
  disclaimer: string;
  zones: ZoneDefinition[];
}

export function computeEffectiveArea(
  totalAreaSquareMeters: number,
  excludedAreaSquareMeters: number,
): { ok: true; effectiveAreaSquareMeters: number } | { ok: false; message: string } {
  if (
    !Number.isFinite(totalAreaSquareMeters) ||
    totalAreaSquareMeters <= 0
  ) {
    return {
      ok: false,
      message: "전체 면적은 0보다 커야 합니다.",
    };
  }
  if (
    !Number.isFinite(excludedAreaSquareMeters) ||
    excludedAreaSquareMeters < 0
  ) {
    return {
      ok: false,
      message: "제외 면적은 0 이상이어야 합니다.",
    };
  }
  const effective = totalAreaSquareMeters - excludedAreaSquareMeters;
  if (effective <= 0) {
    return {
      ok: false,
      message:
        "유효 면적(전체 − 제외)이 0 이하입니다. 건물·시설·통제선·비입수 구역 제외량을 확인하세요.",
    };
  }
  return { ok: true, effectiveAreaSquareMeters: effective };
}

export function loadZoneCatalog(path = ZONES_PATH): BeachZoneCatalog {
  if (!existsSync(path)) {
    throw new Error(`구역 설정 파일을 찾을 수 없습니다: ${path}`);
  }
  const catalog = JSON.parse(readFileSync(path, "utf-8")) as BeachZoneCatalog;
  for (const zone of catalog.zones) {
    if (
      zone.effectiveAreaSquareMeters === undefined ||
      zone.effectiveAreaSquareMeters === null
    ) {
      const computed = computeEffectiveArea(
        zone.totalAreaSquareMeters,
        zone.excludedAreaSquareMeters ?? 0,
      );
      if (!computed.ok) {
        throw new Error(`${zone.zoneId}: ${computed.message}`);
      }
      zone.effectiveAreaSquareMeters = computed.effectiveAreaSquareMeters;
    }
    if (zone.effectiveAreaSquareMeters <= 0) {
      throw new Error(
        `${zone.zoneId}: 유효 면적이 누락되었거나 0 이하입니다.`,
      );
    }
  }
  return catalog;
}

export function getZoneById(
  zoneId: string,
  catalog?: BeachZoneCatalog,
): ZoneDefinition {
  const cat = catalog ?? loadZoneCatalog();
  const zone = cat.zones.find((z) => z.zoneId === zoneId);
  if (!zone) {
    throw new Error(`알 수 없는 구역 ID: ${zoneId}`);
  }
  return zone;
}

/**
 * 부산시설공단 공영주차장 실시간 현황 어댑터
 *
 * 공공데이터포털 오픈 API:
 *   부산시설공단_공영주차장 시설 현황 조회 서비스 (dataSetSn: 15157490)
 *   엔드포인트: https://apis.data.go.kr/B551210/bisco/getParkingLotStatus
 *
 * 포털 URL: https://bsparking.bisco.or.kr/realtimeparking
 * 등록일: 2026-02-26 | 업데이트: 실시간 | 무료 | 자동승인
 *
 * 해운대 해수욕장 주변 공영주차장 (부산시설공단 운영):
 *   - 미포공영주차장         97면   (051-860-7701)
 *   - 해운대광장주차장       120면  (051-860-7715)
 *   - 동백사거리공영주차장    36면   (051-860-7719)
 *
 * 해운대구청 운영 주차장:
 *   - 동백공영주차장         71면   (051-749-4882)
 *   - 동백공원공영주차장     129면  (051-749-4885)
 *   - 문탠로드관광공영주차장  98면   (051-749-4882)
 *   - 송림공원주차장         200면  (051-749-4885)
 *
 * 활용:
 *   - 주차장 혼잡도를 AuxiliaryRiskFactors.entranceCongestion 보조 지표로 반영
 *   - 주차 포화 → 해변 유입 인원 급증 예측 신호로 활용
 */

import type { AuxiliaryRiskFactors } from "../types/index.js";

// ─── 응답 형식 ────────────────────────────────────────────────────────────────

export interface ParkingLotStatus {
  /** 주차장 고유 ID */
  parkingId: string;
  /** 주차장명 */
  parkingName: string;
  /** 최대 주차 가능 대수 */
  maxCapacity: number;
  /** 현재 주차 대수 */
  currentCount: number;
  /** 주차 가능 대수 */
  availableSpaces: number;
  /** 만차 여부 */
  isFull: boolean;
  /** 점유율 (0.0 ~ 1.0) */
  occupancyRate: number;
  /** 혼잡 수준 */
  congestionLevel: "원활" | "보통" | "혼잡" | "만차";
  /** 최종 갱신 시각 (ISO-8601) */
  updatedAt: string;
  /** 위도 */
  lat?: number;
  /** 경도 */
  lng?: number;
  /** 운영 기관 */
  operator?: string;
  /** 전화번호 */
  tel?: string;
}

export interface ParkingStatusSummary {
  /** 전체 공영주차장 목록 */
  lots: ParkingLotStatus[];
  /** 전체 최대 수용 */
  totalCapacity: number;
  /** 현재 전체 주차 대수 */
  totalCurrentCount: number;
  /** 전체 점유율 */
  overallOccupancyRate: number;
  /** 만차 주차장 수 */
  fullCount: number;
  /** 해수욕장 접근 혼잡 예측 */
  beachAccessCongestion: boolean;
  /** 데이터 소스 */
  isMock: boolean;
  /** 조회 시각 */
  fetchedAt: string;
}

// ─── 해운대 공영주차장 목록 (정적 기본값) ────────────────────────────────────

export const HAEUNDAE_PARKING_LOTS: Omit<
  ParkingLotStatus,
  "currentCount" | "availableSpaces" | "isFull" | "occupancyRate" | "congestionLevel" | "updatedAt"
>[] = [
  {
    parkingId: "BSP-MIPO-001",
    parkingName: "미포공영주차장",
    maxCapacity: 97,
    lat: 35.1660,
    lng: 129.1648,
    operator: "부산시설공단",
    tel: "051-860-7701",
  },
  {
    parkingId: "BSP-HSGWANG-001",
    parkingName: "해운대광장주차장",
    maxCapacity: 120,
    lat: 35.1582,
    lng: 129.1611,
    operator: "부산시설공단",
    tel: "051-860-7715",
  },
  {
    parkingId: "BSP-DONGBAEK-SA-001",
    parkingName: "동백사거리공영주차장",
    maxCapacity: 36,
    lat: 35.1553,
    lng: 129.1558,
    operator: "부산시설공단",
    tel: "051-860-7719",
  },
  {
    parkingId: "BSP-DONGBAEK-001",
    parkingName: "동백공영주차장",
    maxCapacity: 71,
    lat: 35.1545,
    lng: 129.1547,
    operator: "해운대구청",
    tel: "051-749-4882",
  },
  {
    parkingId: "BSP-DONGBAEK-PARK-001",
    parkingName: "동백공원공영주차장",
    maxCapacity: 129,
    lat: 35.1540,
    lng: 129.1538,
    operator: "해운대구청",
    tel: "051-749-4885",
  },
  {
    parkingId: "BSP-MOONTAN-001",
    parkingName: "문탠로드관광공영주차장",
    maxCapacity: 98,
    lat: 35.1620,
    lng: 129.1572,
    operator: "해운대구청",
    tel: "051-749-4882",
  },
  {
    parkingId: "BSP-SONGNIM-001",
    parkingName: "송림공원주차장",
    maxCapacity: 200,
    lat: 35.1560,
    lng: 129.1595,
    operator: "해운대구청",
    tel: "051-749-4885",
  },
];

// ─── Mock 생성 헬퍼 ───────────────────────────────────────────────────────────

function makeMockStatus(
  base: (typeof HAEUNDAE_PARKING_LOTS)[number],
  currentCount: number,
): ParkingLotStatus {
  const avail = Math.max(0, base.maxCapacity - currentCount);
  const rate = currentCount / base.maxCapacity;
  let level: ParkingLotStatus["congestionLevel"];
  if (rate >= 1) level = "만차";
  else if (rate >= 0.85) level = "혼잡";
  else if (rate >= 0.6) level = "보통";
  else level = "원활";
  return {
    ...base,
    currentCount,
    availableSpaces: avail,
    isFull: avail === 0,
    occupancyRate: Math.round(rate * 1000) / 1000,
    congestionLevel: level,
    updatedAt: new Date().toISOString(),
  };
}

function buildMockSummary(): ParkingStatusSummary {
  // 성수기 낮 시간대 가상 점유율 시뮬레이션
  const hour = new Date().getHours();
  const isRush = hour >= 10 && hour <= 16;
  const occupancyFactors = [0.95, 1.0, 0.85, 0.75, 0.9, 0.7, 0.88];
  const lots = HAEUNDAE_PARKING_LOTS.map((base, i) => {
    const factor = isRush ? occupancyFactors[i] ?? 0.8 : (occupancyFactors[i] ?? 0.8) * 0.4;
    return makeMockStatus(base, Math.round(base.maxCapacity * factor));
  });
  return buildSummary(lots, true);
}

function buildSummary(lots: ParkingLotStatus[], isMock: boolean): ParkingStatusSummary {
  const totalCapacity = lots.reduce((s, l) => s + l.maxCapacity, 0);
  const totalCurrentCount = lots.reduce((s, l) => s + l.currentCount, 0);
  const overallOccupancyRate =
    totalCapacity > 0
      ? Math.round((totalCurrentCount / totalCapacity) * 1000) / 1000
      : 0;
  const fullCount = lots.filter((l) => l.isFull).length;
  // 주차장 70% 이상 만차 → 해변 접근 혼잡 신호
  const beachAccessCongestion =
    lots.filter((l) => l.occupancyRate >= 0.85).length >= Math.ceil(lots.length * 0.7);
  return {
    lots,
    totalCapacity,
    totalCurrentCount,
    overallOccupancyRate,
    fullCount,
    beachAccessCongestion,
    isMock,
    fetchedAt: new Date().toISOString(),
  };
}

// ─── 어댑터 클래스 ────────────────────────────────────────────────────────────

export interface ParkingAdapterOptions {
  /** 공공데이터포털 인증키. 없으면 Mock 모드 */
  apiKey?: string;
  baseUrl?: string;
}

const DEFAULT_PARKING_BASE_URL =
  "https://apis.data.go.kr/B551210/bisco/getParkingLotStatus";

export class ParkingAdapter {
  private readonly apiKey: string | undefined;
  private readonly baseUrl: string;

  constructor(opts: ParkingAdapterOptions = {}) {
    this.apiKey = opts.apiKey ?? process.env["BUSAN_PARKING_API_KEY"];
    this.baseUrl = opts.baseUrl ?? DEFAULT_PARKING_BASE_URL;
  }

  get isMockMode(): boolean {
    return !this.apiKey;
  }

  /**
   * 공영주차장 실시간 현황 조회
   * - API 키 없음 → Mock 모드 (성수기 낮 시간대 시뮬레이션)
   */
  async fetchParkingStatus(): Promise<ParkingStatusSummary> {
    if (this.isMockMode) {
      return buildMockSummary();
    }

    const url = new URL(this.baseUrl);
    url.searchParams.set("serviceKey", this.apiKey!);
    url.searchParams.set("numOfRows", "50");
    url.searchParams.set("pageNo", "1");
    url.searchParams.set("type", "json");

    const resp = await fetch(url.toString());
    if (!resp.ok) {
      throw new Error(`ParkingAdapter: HTTP ${resp.status} — ${url.toString()}`);
    }

    const json = await resp.json() as {
      response: {
        body: {
          items: {
            item: Array<{
              parkingCd: string;
              parkingNm: string;
              totalParking: number;
              nowParking: number;
              lastUpdtDt: string;
            }>;
          };
        };
      };
    };

    const rawItems = json.response?.body?.items?.item ?? [];
    const lots: ParkingLotStatus[] = rawItems.map((item) => {
      const base = HAEUNDAE_PARKING_LOTS.find(
        (b) => b.parkingName.includes(item.parkingNm) || item.parkingNm.includes(b.parkingName),
      );
      return makeMockStatus(
        base ?? {
          parkingId: item.parkingCd,
          parkingName: item.parkingNm,
          maxCapacity: item.totalParking,
        },
        item.nowParking,
      );
    });

    return buildSummary(lots, false);
  }

  /**
   * 주차 혼잡도를 AuxiliaryRiskFactors 형태로 변환
   * (밀집도 엔진에 보조 지표로 주입 가능)
   */
  async toAuxiliaryRiskFactors(): Promise<Pick<AuxiliaryRiskFactors, "entranceCongestion">> {
    const summary = await this.fetchParkingStatus();
    return {
      entranceCongestion: summary.beachAccessCongestion,
    };
  }

  /**
   * 개별 주차장 현황 조회 (ID 기준)
   */
  async fetchById(parkingId: string): Promise<ParkingLotStatus | null> {
    const summary = await this.fetchParkingStatus();
    return summary.lots.find((l) => l.parkingId === parkingId) ?? null;
  }
}

/** 싱글톤 인스턴스 */
export const sharedParkingAdapter = new ParkingAdapter();

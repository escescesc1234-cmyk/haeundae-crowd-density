/**
 * 부산광역시 ITS 교통 CCTV 어댑터
 *
 * 공공데이터포털 오픈 API:
 *   부산광역시_CCTV 설치 현황정보  (dataSetSn: 15120867)
 *   엔드포인트: https://apis.data.go.kr/6260000/BusanCCTVService/getCCTVInfo
 *
 * 제공 데이터: 교통 CCTV 설치위치, 영상 스트림 URL, 경도·위도
 *
 * 실시간 영상 뷰어: https://its.busan.go.kr/trf/cctv.do
 *
 * 사용법:
 *   1. data.go.kr에서 서비스 활용 신청 후 인증키를 .env > BUSAN_ITS_API_KEY 에 입력
 *   2. BusanItsCctvAdapter.fetchCctvList() 로 카메라 목록을 받아 영상 URL 확인
 *   3. getCctvFeedAsInput() 로 특정 구역의 CCTV 프레임을 DensityInput 형태로 변환
 *
 * 주의:
 *   - 이 어댑터는 CCTV 스트림 URL을 반환하며 실제 객체 감지(detection)는
 *     별도 AI 파이프라인(YOLOv8 등)에서 수행 후 CctvFramePayload로 전달해야 합니다.
 *   - 얼굴 인식·개인 신원 확인 기능을 추가하지 않습니다.
 */

import type { DensityInput } from "../types/index.js";
import type { CctvFramePayload } from "./cctvAdapter.js";
import { toCctvDensityInput } from "./cctvAdapter.js";

// ─── 공공데이터포털 응답 형식 ─────────────────────────────────────────────────

export interface BusanItsCctvItem {
  /** CCTV 고유 ID */
  cctvId: string;
  /** 설치 위치 설명 */
  cctvName: string;
  /** 영상 스트림 URL (HLS / RTSP) */
  streamUrl: string;
  /** 경도 */
  longitude: number;
  /** 위도 */
  latitude: number;
  /** 설치 도로명 */
  roadName?: string;
  /** 관리 기관 */
  agency?: string;
}

interface DataGoKrResponse<T> {
  response: {
    header: { resultCode: string; resultMsg: string };
    body: {
      items: { item: T[] } | T[];
      numOfRows: number;
      pageNo: number;
      totalCount: number;
    };
  };
}

// ─── 어댑터 설정 ──────────────────────────────────────────────────────────────

export interface BusanItsCctvAdapterOptions {
  /** 공공데이터포털 인증키 (URL-encoded). 없으면 Mock 모드 */
  apiKey?: string;
  /** 최대 응답 수 (기본 100) */
  numOfRows?: number;
  /** API 기본 URL */
  baseUrl?: string;
}

const DEFAULT_BASE_URL =
  "https://apis.data.go.kr/6260000/BusanCCTVService/getCCTVInfo";

// ─── Mock 데이터 (API 키 미설정 시) ──────────────────────────────────────────
// 출처: 부산광역시 교통정보서비스센터 공개 정보 기반 예시값
const MOCK_CCTV_LIST: BusanItsCctvItem[] = [
  {
    cctvId: "BSN-HAEUNDAE-001",
    cctvName: "해운대해변로 해운대역사거리",
    streamUrl: "rtsp://its.busan.go.kr/live/haeundae_001",
    longitude: 129.1604,
    latitude: 35.1587,
    roadName: "해운대해변로",
    agency: "부산광역시 교통정보서비스센터",
  },
  {
    cctvId: "BSN-HAEUNDAE-002",
    cctvName: "해운대해변로 동백사거리",
    streamUrl: "rtsp://its.busan.go.kr/live/haeundae_002",
    longitude: 129.1572,
    latitude: 35.1556,
    roadName: "해운대해변로",
    agency: "부산광역시 교통정보서비스센터",
  },
  {
    cctvId: "BSN-HAEUNDAE-003",
    cctvName: "미포공영주차장 입구",
    streamUrl: "rtsp://its.busan.go.kr/live/haeundae_003",
    longitude: 129.1660,
    latitude: 35.1622,
    roadName: "해운대해변로",
    agency: "부산광역시 교통정보서비스센터",
  },
  {
    cctvId: "BSN-GWANGALLI-001",
    cctvName: "광안리 수영민락항 앞",
    streamUrl: "rtsp://its.busan.go.kr/live/gwangalli_001",
    longitude: 129.1187,
    latitude: 35.1528,
    roadName: "광안해변로",
    agency: "부산광역시 교통정보서비스센터",
  },
  {
    cctvId: "BSN-GWANGALLI-002",
    cctvName: "광안대교 하부 해변",
    streamUrl: "rtsp://its.busan.go.kr/live/gwangalli_002",
    longitude: 129.1186,
    latitude: 35.1532,
    roadName: "광안해변로",
    agency: "부산광역시 교통정보서비스센터",
  },
];

// ─── 어댑터 클래스 ────────────────────────────────────────────────────────────

export class BusanItsCctvAdapter {
  private readonly apiKey: string | undefined;
  private readonly numOfRows: number;
  private readonly baseUrl: string;

  constructor(opts: BusanItsCctvAdapterOptions = {}) {
    this.apiKey = opts.apiKey ?? process.env["BUSAN_ITS_API_KEY"];
    this.numOfRows = opts.numOfRows ?? 100;
    this.baseUrl = opts.baseUrl ?? DEFAULT_BASE_URL;
  }

  get isMockMode(): boolean {
    return !this.apiKey;
  }

  /**
   * ITS CCTV 목록 조회
   * - API 키 없음 → Mock 데이터 반환
   * - API 키 있음 → 공공데이터포털 실제 호출
   */
  async fetchCctvList(pageNo = 1): Promise<{
    items: BusanItsCctvItem[];
    totalCount: number;
    isMock: boolean;
  }> {
    if (this.isMockMode) {
      return { items: MOCK_CCTV_LIST, totalCount: MOCK_CCTV_LIST.length, isMock: true };
    }

    const url = new URL(this.baseUrl);
    url.searchParams.set("serviceKey", this.apiKey!);
    url.searchParams.set("numOfRows", String(this.numOfRows));
    url.searchParams.set("pageNo", String(pageNo));
    url.searchParams.set("type", "json");

    const resp = await fetch(url.toString());
    if (!resp.ok) {
      throw new Error(
        `BusanItsCctvAdapter: HTTP ${resp.status} — ${url.toString()}`,
      );
    }

    const json = (await resp.json()) as DataGoKrResponse<BusanItsCctvItem>;
    const body = json.response.body;
    const rawItems = Array.isArray(body.items)
      ? body.items
      : (body.items as { item: BusanItsCctvItem[] }).item ?? [];

    return {
      items: rawItems,
      totalCount: body.totalCount,
      isMock: false,
    };
  }

  /**
   * 해수욕장 주변 CCTV만 필터링 (위치명 또는 키워드 기반)
   */
  async fetchBeachCctvList(
    beachKeyword: "haeundae" | "gwangalli" | "all",
  ): Promise<BusanItsCctvItem[]> {
    const { items } = await this.fetchCctvList();
    if (beachKeyword === "all") return items;

    const keywordMap: Record<string, string[]> = {
      haeundae: ["해운대", "미포", "동백", "마린시티"],
      gwangalli: ["광안리", "광안", "민락"],
    };
    const keywords = keywordMap[beachKeyword] ?? [];
    return items.filter((item) =>
      keywords.some(
        (kw) =>
          item.cctvName.includes(kw) || (item.roadName ?? "").includes(kw),
      ),
    );
  }

  /**
   * AI 파이프라인에서 받은 감지 결과를 DensityInput으로 변환
   * (실제 영상 분석은 외부 YOLOv8/AI 모듈에서 처리 후 payload를 전달)
   */
  toDensityInput(payload: CctvFramePayload): DensityInput {
    return {
      ...toCctvDensityInput(payload),
      dataSource: "busan_its_cctv",
    };
  }
}

/** 싱글톤 인스턴스 (환경변수 자동 로드) */
export const sharedBusanItsCctvAdapter = new BusanItsCctvAdapter();

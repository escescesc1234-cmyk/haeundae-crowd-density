/**
 * 해운대구 CCTV 정보 조회 어댑터
 *
 * 공공데이터포털 오픈 API:
 *   부산광역시 해운대구_CCTV 정보 조회 서비스 (dataSetSn: 15070811)
 *   엔드포인트: http://apis.data.go.kr/3330000/HeaundaeCctvInfoService/getCctvList
 *
 * 제공 데이터: 설치지역, 주소, 전화번호, 관리기관, 위도, 경도
 *
 * 활용 승인: 개발계정 자동승인 / 운영계정 심의승인
 * 트래픽: 개발계정 10,000건/일
 *
 * 용도:
 *   - 구역별 가장 가까운 CCTV를 매핑하여 DensityInput 소스 지정
 *   - 관리자 화면에서 구역별 CCTV 위치 시각화
 *   - AI-Hub 이안류 데이터셋 카메라 ID와의 매핑 참고
 *
 * AI-Hub 이안류 CCTV 데이터셋 (dataSetSn: 71297)
 *   해운대 카메라: GLORY, PARA1, PARA2, SEAC1 (총 4대)
 *   포맷: HD 1,280×720 JPG + JSON 라벨 (바운딩박스)
 *   다운로드: https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297
 *
 * AI-Hub 실내외 군중 특성 데이터셋 (dataSetSn: 71368)
 *   총 228,195장 이미지 + MP4 400개
 *   라벨: 군중 계수, 밀집도(High/Mid/Low), 헤드포인트 좌표
 *   다운로드: https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71368
 */

// ─── 응답 형식 ────────────────────────────────────────────────────────────────

export interface HaeundaeCctvItem {
  /** 설치지역 (예: "해운대해수욕장", "동백공영주차장") */
  area: string;
  /** 도로명 주소 */
  address: string;
  /** 담당자 전화번호 */
  tel: string;
  /** 관리기관 (예: "안전총괄과", "관광시설사업소") */
  charge: string;
  /** 위도 */
  lat: number;
  /** 경도 */
  lng: number;
  /** 시설구분 */
  clsName?: string;
}

// ─── AI-Hub 이안류 카메라 매핑 ───────────────────────────────────────────────

export interface AiHubCameraInfo {
  /** AI-Hub 데이터셋 내 카메라 ID */
  cameraId: string;
  /** 실제 설치 위치명 */
  locationName: string;
  /** AI-Hub 데이터셋 번호 */
  dataSetSn: number;
  /** 데이터셋 URL */
  dataSetUrl: string;
  /** 총 이미지 수 */
  totalImages: number;
  /** 이안류 발생 이미지 수 */
  ripCurrentImages: number;
  /** 화질 */
  resolution: string;
}

/** 해운대 AI-Hub 이안류 CCTV 카메라 목록 (공식 데이터셋 기준) */
export const HAEUNDAE_AIHUB_CAMERAS: AiHubCameraInfo[] = [
  {
    cameraId: "GLORY",
    locationName: "해운대 글로리 포인트",
    dataSetSn: 71297,
    dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297",
    totalImages: 26207,
    ripCurrentImages: 5001,
    resolution: "HD 1,280×720",
  },
  {
    cameraId: "PARA1",
    locationName: "해운대 파라솔 구역 1",
    dataSetSn: 71297,
    dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297",
    totalImages: 39348,
    ripCurrentImages: 7085,
    resolution: "HD 1,280×720",
  },
  {
    cameraId: "PARA2",
    locationName: "해운대 파라솔 구역 2",
    dataSetSn: 71297,
    dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297",
    totalImages: 26987,
    ripCurrentImages: 5408,
    resolution: "HD 1,280×720",
  },
  {
    cameraId: "SEAC1",
    locationName: "해운대 씨클라우드 1",
    dataSetSn: 71297,
    dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297",
    totalImages: 17673,
    ripCurrentImages: 3540,
    resolution: "HD 1,280×720",
  },
];

/** 광안리 AI-Hub 이안류 CCTV 카메라 목록 */
export const GWANGALLI_AIHUB_CAMERAS: AiHubCameraInfo[] = [
  {
    cameraId: "SJHT1",
    locationName: "광안리 히트 구역 1",
    dataSetSn: 71297,
    dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297",
    totalImages: 63845,
    ripCurrentImages: 12032,
    resolution: "HD 1,280×720",
  },
  {
    cameraId: "WHIB1",
    locationName: "광안리 화이트비치 1",
    dataSetSn: 71297,
    dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297",
    totalImages: 16076,
    ripCurrentImages: 3210,
    resolution: "HD 1,280×720",
  },
];

// ─── 군중 특성 데이터셋 메타 ──────────────────────────────────────────────────

export const CROWD_CHARACTERISTIC_DATASET = {
  dataSetSn: 71368,
  name: "실내외 군중 특성 데이터",
  dataSetUrl: "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71368",
  totalImages: 228195,
  mp4Count: 400,
  labels: {
    counting: "군중 계수",
    collectiveness: "밀집도 (High≥50명 / Mid 13-49명 / Low≤12명)",
    stability: "안정도 (High/Low)",
    uniformity: "균일도 (High/Low)",
    headPoint: "헤드포인트 좌표 [x, y]",
    boundingBox: "바운딩박스 좌표",
  },
  note: "CCTV 원천 데이터 + 시나리오 촬영 데이터 포함. 군중 밀도 모델 학습에 직접 활용 가능.",
} as const;

// ─── 어댑터 ───────────────────────────────────────────────────────────────────

export interface HaeundaeCctvAdapterOptions {
  /** 공공데이터포털 인증키. 없으면 Mock 모드 */
  apiKey?: string;
  numOfRows?: number;
}

const DEFAULT_ENDPOINT =
  "http://apis.data.go.kr/3330000/HeaundaeCctvInfoService/getCctvList";

const MOCK_ITEMS: HaeundaeCctvItem[] = [
  {
    area: "해운대해수욕장",
    address: "부산광역시 해운대구 해운대해변로 264",
    tel: "051-749-4640",
    charge: "재난안전과",
    lat: 35.1587,
    lng: 129.1604,
    clsName: "CCTV",
  },
  {
    area: "동백공영주차장",
    address: "부산광역시 해운대구 동백로 1151-3",
    tel: "051-749-4882",
    charge: "교통행정과",
    lat: 35.1545,
    lng: 129.1547,
    clsName: "CCTV",
  },
  {
    area: "미포공영주차장",
    address: "부산광역시 해운대구 해운대해변로 1778-2",
    tel: "051-860-7701",
    charge: "부산시설공단",
    lat: 35.1660,
    lng: 129.1648,
    clsName: "CCTV",
  },
  {
    area: "해운대광장주차장",
    address: "부산광역시 해운대구 해운대해변로 622-4",
    tel: "051-860-7715",
    charge: "부산시설공단",
    lat: 35.1582,
    lng: 129.1611,
    clsName: "CCTV",
  },
  {
    area: "동백사거리공영주차장",
    address: "부산광역시 해운대구 우1동 1437",
    tel: "051-860-7719",
    charge: "부산시설공단",
    lat: 35.1553,
    lng: 129.1558,
    clsName: "CCTV",
  },
  {
    area: "문탠로드관광공영주차장",
    address: "부산광역시 해운대구 중1동 974-1",
    tel: "051-749-4882",
    charge: "관광시설사업소",
    lat: 35.1620,
    lng: 129.1572,
    clsName: "CCTV",
  },
  {
    area: "광안리해수욕장 민락수변공원",
    address: "부산광역시 수영구 광안해변로 219",
    tel: "051-610-4000",
    charge: "수영구",
    lat: 35.1532,
    lng: 129.1186,
    clsName: "CCTV",
  },
];

export class HaeundaeCctvAdapter {
  private readonly apiKey: string | undefined;
  private readonly numOfRows: number;

  constructor(opts: HaeundaeCctvAdapterOptions = {}) {
    this.apiKey = opts.apiKey ?? process.env["HAEUNDAE_CCTV_API_KEY"];
    this.numOfRows = opts.numOfRows ?? 200;
  }

  get isMockMode(): boolean {
    return !this.apiKey;
  }

  /** 해운대구 전체 CCTV 목록 조회 */
  async fetchCctvList(): Promise<{ items: HaeundaeCctvItem[]; isMock: boolean }> {
    if (this.isMockMode) {
      return { items: MOCK_ITEMS, isMock: true };
    }

    const url = new URL(DEFAULT_ENDPOINT);
    url.searchParams.set("serviceKey", this.apiKey!);
    url.searchParams.set("numOfRows", String(this.numOfRows));
    url.searchParams.set("pageNo", "1");
    url.searchParams.set("type", "json");

    const resp = await fetch(url.toString());
    if (!resp.ok) {
      throw new Error(`HaeundaeCctvAdapter: HTTP ${resp.status}`);
    }
    const json = await resp.json() as { response: { body: { items: { item: HaeundaeCctvItem[] } } } };
    const items = json.response?.body?.items?.item ?? [];
    return { items, isMock: false };
  }

  /** 주차장 구역 CCTV만 필터링 */
  async fetchParkingCctvList(): Promise<HaeundaeCctvItem[]> {
    const { items } = await this.fetchCctvList();
    const parkingKeywords = ["주차장", "주차", "parking"];
    return items.filter((item) =>
      parkingKeywords.some((kw) =>
        item.area.toLowerCase().includes(kw) ||
        item.address.toLowerCase().includes(kw),
      ),
    );
  }

  /** 특정 구역과 가장 가까운 CCTV를 찾아 반환 */
  findNearestCctv(
    lat: number,
    lng: number,
    items?: HaeundaeCctvItem[],
  ): HaeundaeCctvItem | null {
    const list = items ?? MOCK_ITEMS;
    if (list.length === 0) return null;
    return list.reduce((nearest, item) => {
      const d1 = haversineDistance(lat, lng, item.lat, item.lng);
      const d2 = haversineDistance(lat, lng, nearest.lat, nearest.lng);
      return d1 < d2 ? item : nearest;
    });
  }

  /** AI-Hub 이안류 카메라 메타 반환 */
  getAiHubCameraInfo(beachId: "haeundae" | "gwangalli"): AiHubCameraInfo[] {
    return beachId === "haeundae"
      ? HAEUNDAE_AIHUB_CAMERAS
      : GWANGALLI_AIHUB_CAMERAS;
  }

  /** 군중 특성 데이터셋 메타 반환 */
  getCrowdDatasetInfo() {
    return CROWD_CHARACTERISTIC_DATASET;
  }
}

/** 두 좌표 간 거리(m) — Haversine 공식 */
function haversineDistance(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** 싱글톤 인스턴스 */
export const sharedHaeundaeCctvAdapter = new HaeundaeCctvAdapter();

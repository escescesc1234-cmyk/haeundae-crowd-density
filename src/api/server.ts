import express from "express";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { sharedService } from "../service/crowdDensityService.js";
import { sharedNotificationService } from "../notification/service.js";
import { sharedForecastService } from "../forecast/service.js";
import { sharedTelecomProvider } from "../forecast/telecomProvider.js";
import { toManualDensityInput } from "../adapters/manualAdapter.js";
import { toTouristList, getBeachWideJudgment } from "../views/touristView.js";
import { toAdminList } from "../views/adminView.js";
import { buildWaveGuardDashboard } from "../views/waveguardDashboard.js";
import { validateThresholdOrder } from "../config/thresholds.js";
import { sharedBusanItsCctvAdapter } from "../adapters/busanItsCctvAdapter.js";
import { sharedHaeundaeCctvAdapter } from "../adapters/haeundaeCctvAdapter.js";
import { sharedParkingAdapter } from "../adapters/parkingAdapter.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const publicDir = join(__dirname, "..", "..", "public");

/** 서버→비전 프록시용 (Docker 내부망: http://vision:8790) */
function realtimeSafetyInternalBase(): string {
  return (
    process.env.REALTIME_SAFETY_URL?.replace(/\/$/, "") ??
    "http://127.0.0.1:8790"
  );
}

/** 브라우저에 내려줄 공개 URL (배포 시 REALTIME_SAFETY_PUBLIC_URL) */
function realtimeSafetyPublicBase(): string {
  return (
    process.env.REALTIME_SAFETY_PUBLIC_URL?.replace(/\/$/, "") ??
    realtimeSafetyInternalBase()
  );
}

/** 타 앱(다른 포트/오리진) 브라우저 연동용 CORS. CORS_ORIGINS=* 또는 쉼표 목록 */
function applyCors(
  req: express.Request,
  res: express.Response,
): void {
  const raw = (process.env.CORS_ORIGINS ?? "*").trim();
  const origin = req.headers.origin;
  if (raw === "*") {
    res.setHeader("Access-Control-Allow-Origin", "*");
  } else if (origin && raw.split(",").map((s) => s.trim()).includes(origin)) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
  }
  res.setHeader(
    "Access-Control-Allow-Methods",
    "GET,POST,PUT,PATCH,DELETE,OPTIONS",
  );
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Content-Type, Authorization",
  );
}

async function proxyRealtimeJson(
  path: string,
  timeoutMs = 8_000,
): Promise<{ ok: true; data: unknown } | { ok: false; error: string; status: number }> {
  const url = `${realtimeSafetyInternalBase()}${path.startsWith("/") ? path : `/${path}`}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    const text = await res.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text) as unknown;
      } catch {
        data = text;
      }
    }
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        error: `실시간 비전 서비스 오류 (HTTP ${res.status})`,
      };
    }
    return { ok: true, data };
  } catch (err) {
    const msg =
      err instanceof Error && err.name === "AbortError"
        ? `실시간 비전 서비스 타임아웃 (${timeoutMs}ms)`
        : `실시간 비전 서비스 연결 실패: ${
            err instanceof Error ? err.message : String(err)
          }`;
    return { ok: false, status: 502, error: msg };
  } finally {
    clearTimeout(timer);
  }
}

export function createApp() {
  const app = express();
  app.use(express.json({ limit: "1mb" }));
  app.use((req, res, next) => {
    applyCors(req, res);
    if (req.method === "OPTIONS") {
      res.status(204).end();
      return;
    }
    next();
  });
  app.use(express.static(publicDir));

  app.get("/api/health", (_req, res) => {
    res.json({
      ok: true,
      service: "haeundae-crowd-density",
      disclaimer:
        "밀도·등급은 현장 판단을 돕는 참고 정보이며 절대적 사고 예측이 아닙니다.",
    });
  });

  /** 브라우저 DensityApiClient용 baseUrl 안내 (비밀키 없음) */
  app.get("/api/client-config", (_req, res) => {
    const configured = (process.env.DENSITY_API_BASE_URL ?? "").replace(
      /\/$/,
      "",
    );
    const realtime = realtimeSafetyPublicBase();
    res.json({
      densityApiBaseUrl: configured,
      defaultZoneId: "GWANGALLI-ZONE-CENTER",
      port: Number(process.env.PORT ?? 3780),
      realtimeSafetyUrl: realtime,
      visionModelWeight: "vision/models/yolo26s_beach_ft.pt",
      note: "동일 오리진 UI는 상대 경로로 API를 호출해도 됩니다. 타 앱은 DENSITY_API_BASE_URL + CORS 사용.",
    });
  });

  app.get("/api/zones", (_req, res) => {
    res.json(sharedService.getCatalog());
  });

  app.get("/api/thresholds", (_req, res) => {
    res.json(sharedService.getThresholds());
  });

  app.get("/api/thresholds/history", (_req, res) => {
    res.json(sharedService.getThresholdChangeLog());
  });

  app.put("/api/thresholds", (req, res) => {
    try {
      const { changedBy, reason, targetZoneIds, fieldVerified, ...partial } =
        req.body ?? {};
      if (!changedBy || !reason) {
        res.status(400).json({
          error: "changedBy와 reason은 필수입니다.",
        });
        return;
      }
      const check = validateThresholdOrder({
        ...sharedService.getThresholds(),
        ...partial,
      });
      if (!check.valid) {
        res.status(400).json({ error: check.message });
        return;
      }
      const saved = sharedService.updateThresholds(partial, {
        changedBy,
        reason,
        targetZoneIds,
        fieldVerified,
      });
      res.json(saved);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/analyze", (req, res) => {
    try {
      const skipHysteresis = Boolean(req.body?.skipHysteresis);
      const result = sharedService.analyze(req.body, { skipHysteresis });
      res.json(result);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/analyze/manual", async (req, res) => {
    try {
      const notify = Boolean(req.body?.notify);
      const input = toManualDensityInput({
        zoneId: req.body.zoneId,
        detectedPeople: req.body.detectedPeople,
        effectiveAreaSquareMeters: req.body.effectiveAreaSquareMeters,
        measuredAt: req.body.measuredAt,
        confidence: req.body.confidence,
        dataSource: req.body.dataSource ?? "manual",
        isTestData: req.body.isTestData,
      });
      if (notify) {
        const payload = await sharedService.analyzeAndNotify(input, {
          skipHysteresis: Boolean(req.body?.skipHysteresis),
          manualCongestionBroadcast: Boolean(req.body?.manualBroadcast),
        });
        res.json(payload);
        return;
      }
      const result = sharedService.analyze(input, {
        skipHysteresis: Boolean(req.body?.skipHysteresis),
      });
      res.json(result);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/analyze/cctv", (req, res) => {
    try {
      const result = sharedService.analyzeCctv(req.body, {
        skipHysteresis: Boolean(req.body?.skipHysteresis),
      });
      res.json(result);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  /**
   * YOLOv8 + SAHI 비전 분석 → 밀도 엔진 연동
   * body: { imagePath, zoneId?, useHomographyArea?, notify?, skipHysteresis? }
   */
  app.post("/api/analyze/vision", async (req, res) => {
    try {
      const imagePath = req.body?.imagePath as string | undefined;
      if (!imagePath) {
        res.status(400).json({
          error:
            "imagePath 필수 (예: vision/input/screenshots/01_wide_full_beach.png)",
        });
        return;
      }
      const result = await sharedService.analyzeVision(
        {
          imagePath,
          zoneId: req.body?.zoneId,
          calibrationPath: req.body?.calibrationPath,
          useHomographyArea: Boolean(req.body?.useHomographyArea),
          pythonPath: req.body?.pythonPath,
        },
        {
          skipHysteresis: Boolean(req.body?.skipHysteresis),
          notify: Boolean(req.body?.notify),
          manualBroadcast: Boolean(req.body?.manualBroadcast),
        },
      );
      res.json({
        ok: true,
        analysis: result.analysis,
        vision: result.vision.payload.vision,
        alerts: result.vision.payload.alerts,
        densityInput: result.vision.densityInput,
        notification: result.notification,
        disclaimer:
          "비전 밀도는 참고 정보입니다. 호모그래피 미검증 시 구역 카탈로그 면적을 사용합니다.",
      });
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  /** 비전 열지도 등 산출물 정적 제공 */
  app.use(
    "/vision-output",
    express.static(join(__dirname, "..", "..", "vision", "output")),
  );

  /** 실시간 AI 비전 메타 (Python :8790). 타 앱은 이 엔드포인트로 URL·모델 계약을 받는다. */
  app.get("/api/vision/realtime", (_req, res) => {
    const base = realtimeSafetyPublicBase();
    res.json({
      ok: true,
      service: "realtime-safety-map",
      modelWeight: "vision/models/yolo26s_beach_ft.pt",
      uiUrl: `${base}/`,
      /** 앱 기본 스트림 = SAHI-256 실시간 (잘림 없이 9:16 contain 권장) */
      streamUrl: `${base}/stream/sahi256`,
      streamSahi256Url: `${base}/stream/sahi256`,
      streamSafetyMapUrl: `${base}/stream`,
      streamYoloUrl: `${base}/stream/yolo`,
      preferredStream: "sahi256",
      statusUrl: `${base}/api/status`,
      modelInfoUrl: `${base}/api/model-info`,
      /** 동일 오리진 프록시 (타 앱은 baseUrl=3780 만으로도 상태/모델 조회 가능) */
      proxiedStatusPath: "/api/vision/realtime/status",
      proxiedModelInfoPath: "/api/vision/realtime/model",
      /** 타 앱 권장: 숫자+스트림 계약을 한 번에 */
      monitorPath: "/api/vision/realtime/monitor",
      defaultSource: "https://www.youtube.com/watch?v=jmVmZlsQIL8",
      howToStart: "npm run vision:realtime",
      partialOk: true,
      pollIntervalMs: 2000,
      display: {
        aspectRatio: "9:16",
        objectFit: "contain",
        note: "잘림 금지 — object-fit:contain + 레터박스 허용",
      },
      grid: {
        cellW: 40,
        cellH: 15,
        colors: {
          safe: { hex: "#00FF00", maxExclusive: 4, alpha: 0.5 },
          caution: { hex: "#FFFF00", minInclusive: 4, maxExclusive: 6, alpha: 0.5 },
          danger: { hex: "#FF0000", minInclusive: 6, alpha: 0.5 },
        },
      },
      alerts: {
        tourist:
          "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요.",
        manager:
          "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요.",
      },
    });
  });

  /** 실시간 비전 상태 프록시 → :8790/api/status */
  app.get("/api/vision/realtime/status", async (_req, res) => {
    const result = await proxyRealtimeJson("/api/status");
    if (!result.ok) {
      res.status(result.status).json({
        ok: false,
        error: result.error,
        howToStart: "npm run vision:realtime",
      });
      return;
    }
    res.json(result.data);
  });

  /** 로드된 YOLO 가중치 메타 프록시 → :8790/api/model-info */
  app.get("/api/vision/realtime/model", async (_req, res) => {
    const result = await proxyRealtimeJson("/api/model-info");
    if (!result.ok) {
      res.status(result.status).json({
        ok: false,
        error: result.error,
        modelWeight: "vision/models/yolo26s_beach_ft.pt",
        howToStart: "npm run vision:realtime",
      });
      return;
    }
    res.json(result.data);
  });

  /**
   * 타 앱 실시간 모니터링 전용 (한 번에 숫자+스트림 계약).
   * 부분 탐지(일부만 인식)도 정상 — partialOk=true, 숫자는 있는 그대로 표시.
   */
  app.get("/api/vision/realtime/monitor", async (_req, res) => {
    const base = realtimeSafetyPublicBase();
    const result = await proxyRealtimeJson("/api/status", 12_000);
    if (!result.ok) {
      res.status(result.status).json({
        ok: false,
        live: false,
        error: result.error,
        howToStart: [
          "cd haeundae-crowd-density",
          "npm run dev",
          "npm run vision:realtime",
        ],
        streamUrl: `${base}/stream/sahi256`,
        display: {
          aspectRatio: "9:16",
          objectFit: "contain",
          note: "잘림 금지 — contain + 레터박스 허용",
        },
        monitoring: null,
      });
      return;
    }

    const st = (result.data ?? {}) as Record<string, unknown>;
    const sahi = (st.sahi256 ?? {}) as Record<string, unknown>;
    const est = (st.estimatedTotal ?? {}) as Record<string, unknown>;
    const alerts = (st.alerts ?? {}) as Record<string, unknown>;
    const sahiState = String(sahi.state ?? "unknown");
    const sahiEnabled = sahi.enabled !== false;
    const fastPerson = Number(st.personCount ?? 0) || 0;
    const sahiPerson = Number(sahi.personCount ?? 0) || 0;
    const tubeCount = Number(st.tubeCount ?? 0) || 0;
    const estCount =
      typeof est.count === "number"
        ? est.count
        : Math.max(fastPerson + tubeCount, sahiPerson);

    const phase =
      !sahiEnabled
        ? "sahi_disabled"
        : sahiState === "ok" || sahiState === "running"
          ? "live"
          : sahiState === "loading" || sahiState === "idle"
            ? "warming"
            : sahiState === "error"
              ? "error"
              : "live";

    res.json({
      ok: true,
      live: phase === "live" || fastPerson > 0 || sahiPerson > 0,
      phase,
      partialOk: true,
      disclaimer:
        "부분 탐지(화면 일부만 인식)도 정상입니다. 숫자는 참고용이며 전수 인원을 보장하지 않습니다.",
      streamUrl: `${base}/stream/sahi256`,
      streamSahi256Url: `${base}/stream/sahi256`,
      streamSafetyMapUrl: `${base}/stream`,
      streamYoloUrl: `${base}/stream/yolo`,
      pollIntervalMs: 2000,
      display: {
        aspectRatio: "9:16",
        objectFit: "contain",
        note: "잘림 금지 — object-fit:contain + 레터박스 허용",
      },
      monitoring: {
        estimatedTotal: estCount,
        fastPersonCount: fastPerson,
        sahiPersonCount: sahiPerson,
        tubeCount,
        maxGridDensityPerM2: Number(st.maxGridDensityPerM2 ?? 0) || 0,
        sahiState,
        sahiEnabled,
        sahiInferMs: Number(sahi.inferMs ?? 0) || 0,
        sahiUpdatedAt: sahi.updatedAt ?? null,
        fastUpdatedAt: st.updatedAt ?? null,
        pipeline: st.pipeline ?? null,
        detector: st.detector ?? null,
        source: typeof est.source === "string" ? est.source : "detection",
      },
      alerts: {
        hasDanger: Boolean(alerts.hasDanger),
        dangerCellCount: Number(alerts.dangerCellCount ?? 0) || 0,
        touristMessage:
          typeof alerts.touristMessage === "string"
            ? alerts.touristMessage
            : null,
        managerMessage:
          typeof alerts.managerMessage === "string"
            ? alerts.managerMessage
            : null,
      },
      howToStart: "npm run vision:realtime",
    });
  });

  app.get("/api/results", (_req, res) => {
    const results = sharedService.getLatestResults();
    res.json(results);
  });

  app.get("/api/tourist/zones", (_req, res) => {
    const results = sharedService.getLatestResults();
    res.json(toTouristList(results));
  });

  app.get("/api/tourist/beach", (_req, res) => {
    const results = sharedService.getLatestResults();
    res.json({
      beachId: "GWANGALLI",
      beachName: "광안리 해수욕장",
      judgment: getBeachWideJudgment(results),
      zones: toTouristList(results),
    });
  });

  app.get("/api/admin/zones", (_req, res) => {
    res.json(toAdminList(sharedService.getLatestResults()));
  });

  app.post("/api/admin/override", (req, res) => {
    try {
      const { zoneId, riskLevel, by, reason } = req.body ?? {};
      if (!zoneId || !riskLevel || !by || !reason) {
        res.status(400).json({ error: "zoneId, riskLevel, by, reason 필수" });
        return;
      }
      res.json(sharedService.manualOverride({ zoneId, riskLevel, by, reason }));
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/admin/confirm", (req, res) => {
    try {
      res.json(sharedService.confirmZone(req.body.zoneId));
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/admin/alert", (req, res) => {
    try {
      const { zoneId, message, sentBy } = req.body ?? {};
      res.json(sharedService.sendAlert(zoneId, message, sentBy));
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/admin/false-positive", (req, res) => {
    try {
      const { zoneId, reportedRiskLevel, actualNote, recordedBy } =
        req.body ?? {};
      res.json(
        sharedService.recordFalsePositive(
          zoneId,
          reportedRiskLevel,
          actualNote,
          recordedBy,
        ),
      );
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/notifications/process", async (req, res) => {
    try {
      const result = req.body?.analysisResult;
      if (!result) {
        res.status(400).json({ error: "analysisResult 필수" });
        return;
      }
      const processed = await sharedNotificationService.processAnalysisResult(
        result,
        { manualCongestionBroadcast: Boolean(req.body?.manualBroadcast) },
      );
      res.json(processed);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.get("/api/notifications/events", (_req, res) => {
    res.json(sharedNotificationService.listEvents());
  });

  app.get("/api/notifications/deliveries", (req, res) => {
    res.json(
      sharedNotificationService.listDeliveries(
        req.query.eventId as string | undefined,
      ),
    );
  });

  app.post("/api/notifications/acknowledge", (req, res) => {
    try {
      const { eventId, managerId } = req.body ?? {};
      res.json(sharedNotificationService.acknowledge(eventId, managerId));
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/notifications/respond", (req, res) => {
    try {
      const { eventId, managerId } = req.body ?? {};
      res.json(sharedNotificationService.startResponse(eventId, managerId));
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/notifications/resolve", (req, res) => {
    try {
      const { eventId, status } = req.body ?? {};
      res.json(
        sharedNotificationService.resolve(eventId, status ?? "해결됨"),
      );
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/notifications/escalate-check", async (_req, res) => {
    try {
      const escalations = await sharedNotificationService.checkEscalations();
      res.json(escalations);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.get("/api/notifications/channel-logs", (_req, res) => {
    res.json(sharedNotificationService.getChannelLogs());
  });

  /** WaveGuard 관광객·관리자 공통 대시보드 (동일 데이터 소스) */
  app.get("/api/waveguard/dashboard", async (req, res) => {
    try {
      const targetDate =
        (req.query.date as string | undefined) ??
        new Date().toISOString().slice(0, 10);
      const catalog = sharedService.getCatalog();
      const overview = await sharedForecastService.getOverview(
        {
          mode: req.query.mode === "simulation" ? "simulation" : "live",
          targetDate,
          useWeather: true,
          useEventData: true,
        },
        sharedService.getLatestResults(),
      );
      const telecom = await sharedTelecomProvider.getCongestion({
        live: true,
        forceRefresh: req.query.telecomRefresh === "true",
      });
      res.json(
        buildWaveGuardDashboard(
          sharedService.getLatestResults(),
          overview,
          {
            beachId: catalog.beachId,
            beachName: catalog.beachName,
          },
          telecom,
        ),
      );
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.get("/api/forecast/overview", async (req, res) => {
    try {
      const targetDate =
        (req.query.date as string | undefined) ??
        new Date().toISOString().slice(0, 10);
      const mode = req.query.mode === "simulation" ? "simulation" : "live";
      const zoneIds = typeof req.query.zoneIds === "string"
        ? req.query.zoneIds.split(",").filter(Boolean)
        : undefined;
      const overview = await sharedForecastService.getOverview(
        {
          mode,
          targetDate,
          targetTime: req.query.time as string | undefined,
          compareYear: req.query.compareYear
            ? Number(req.query.compareYear)
            : undefined,
          zoneIds,
          useWeather: req.query.useWeather !== "false",
          useEventData: req.query.useEventData !== "false",
          useTelecom: req.query.useTelecom === "true",
          sendTestNotification: req.query.sendTestNotification === "true",
          createdBy: (req.query.createdBy as string | undefined) ?? "admin",
        },
        sharedService.getLatestResults(),
      );
      res.json({
        ...overview,
        oneHourForecast: sharedForecastService.oneHourForecastForLatest(
          overview,
          sharedService.getLatestResults(),
        ),
      });
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.get("/api/weather/current", async (req, res) => {
    try {
      const targetDate =
        (req.query.date as string | undefined) ??
        new Date().toISOString().slice(0, 10);
      const overview = await sharedForecastService.getOverview(
        {
          mode: req.query.mode === "simulation" ? "simulation" : "live",
          targetDate,
          useWeather: true,
          useEventData: false,
        },
        sharedService.getLatestResults(),
      );
      res.json(overview.weather);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.get("/api/population/telecom", async (req, res) => {
    try {
      // 기본: 실호출 없음. live=true 또는 test=true 일 때만 SK API 연결
      const live =
        req.query.live === "true" ||
        req.query.test === "true" ||
        req.query.refresh === "true";
      const telecom = await sharedTelecomProvider.getCongestion({
        live,
        forceRefresh: req.query.refresh === "true",
      });
      res.json(telecom);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/population/telecom/test", async (_req, res) => {
    try {
      const telecom = await sharedTelecomProvider.getCongestion({
        live: true,
        forceRefresh: true,
      });
      res.json(telecom);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.get("/api/weather/compare", async (req, res) => {
    try {
      const targetDate =
        (req.query.date as string | undefined) ??
        new Date().toISOString().slice(0, 10);
      const overview = await sharedForecastService.getOverview(
        {
          mode: req.query.mode === "simulation" ? "simulation" : "live",
          targetDate,
          compareYear: req.query.compareYear
            ? Number(req.query.compareYear)
            : undefined,
          useWeather: true,
          useEventData: true,
        },
        sharedService.getLatestResults(),
      );
      res.json(overview.historicalComparison);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/forecast/simulation", async (req, res) => {
    try {
      const request = {
        mode: "simulation" as const,
        targetDate: req.body?.targetDate,
        targetTime: req.body?.targetTime,
        zoneIds: req.body?.zoneIds,
        compareYear: req.body?.compareYear,
        useWeather: req.body?.useWeather !== false,
        useEventData: req.body?.useEventData !== false,
        sendTestNotification: Boolean(req.body?.sendTestNotification),
        createdBy: req.body?.createdBy ?? "admin",
      };
      const scenario = sharedForecastService.createSimulationScenario(request);
      const overview = await sharedForecastService.getOverview(
        request,
        sharedService.getLatestResults(),
      );
      res.json({ scenario, overview });
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/forecast/backtest", async (req, res) => {
    try {
      const result = await sharedForecastService.runBacktest(req.body?.targetDate);
      res.json(result);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  app.post("/api/forecast/pre-notifications", async (req, res) => {
    try {
      const overview = await sharedForecastService.getOverview(
        {
          mode: req.body?.mode === "simulation" ? "simulation" : "live",
          targetDate:
            req.body?.targetDate ?? new Date().toISOString().slice(0, 10),
          zoneIds: req.body?.zoneIds,
          sendTestNotification: Boolean(req.body?.sendTestNotification),
          createdBy: req.body?.createdBy ?? "admin",
        },
        sharedService.getLatestResults(),
      );
      res.json(overview.proactiveNotifications);
    } catch (err) {
      res.status(400).json({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });

  // ─── 부산 ITS CCTV 엔드포인트 ─────────────────────────────────────────────

  /** 부산 ITS 교통 CCTV 목록 (해수욕장 주변 필터 선택 가능) */
  app.get("/api/cctv/busan-its", async (req, res) => {
    try {
      const beach = (req.query.beach as string | undefined) ?? "all";
      const validBeaches = ["haeundae", "gwangalli", "all"] as const;
      type BeachKey = (typeof validBeaches)[number];
      const beachKey: BeachKey = validBeaches.includes(beach as BeachKey)
        ? (beach as BeachKey)
        : "all";
      const items = await sharedBusanItsCctvAdapter.fetchBeachCctvList(beachKey);
      res.json({
        beach: beachKey,
        count: items.length,
        isMock: sharedBusanItsCctvAdapter.isMockMode,
        items,
        note: sharedBusanItsCctvAdapter.isMockMode
          ? "BUSAN_ITS_API_KEY 미설정 — Mock 데이터입니다. .env에 키를 입력하세요."
          : undefined,
      });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  /** 해운대구 CCTV 목록 */
  app.get("/api/cctv/haeundae-district", async (req, res) => {
    try {
      const type = req.query.type as string | undefined;
      const { items, isMock } = await sharedHaeundaeCctvAdapter.fetchCctvList();
      const filtered = type === "parking"
        ? items.filter((i) => i.area.includes("주차"))
        : items;
      res.json({
        count: filtered.length,
        isMock,
        items: filtered,
        note: isMock
          ? "HAEUNDAE_CCTV_API_KEY 미설정 — Mock 데이터입니다."
          : undefined,
      });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  /** AI-Hub 이안류 카메라 메타 정보 */
  app.get("/api/cctv/aihub-cameras", (req, res) => {
    const beach = (req.query.beach as string | undefined) ?? "haeundae";
    const cameras = sharedHaeundaeCctvAdapter.getAiHubCameraInfo(
      beach === "gwangalli" ? "gwangalli" : "haeundae",
    );
    const crowdDataset = sharedHaeundaeCctvAdapter.getCrowdDatasetInfo();
    res.json({ beach, ripCurrentCameras: cameras, crowdCharacteristicDataset: crowdDataset });
  });

  // ─── 공영주차장 엔드포인트 ──────────────────────────────────────────────────

  /** 공영주차장 실시간 현황 */
  app.get("/api/parking/status", async (_req, res) => {
    try {
      const summary = await sharedParkingAdapter.fetchParkingStatus();
      res.json({
        ...summary,
        note: summary.isMock
          ? "BUSAN_PARKING_API_KEY 미설정 — Mock 데이터(성수기 낮 시간대 시뮬레이션)입니다."
          : undefined,
      });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  /** 특정 주차장 현황 */
  app.get("/api/parking/status/:parkingId", async (req, res) => {
    try {
      const lot = await sharedParkingAdapter.fetchById(req.params.parkingId);
      if (!lot) {
        res.status(404).json({ error: `주차장 ID '${req.params.parkingId}' 없음` });
        return;
      }
      res.json(lot);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  /** 주차 혼잡도를 AuxiliaryRiskFactors 형태로 반환 */
  app.get("/api/parking/auxiliary-risk", async (_req, res) => {
    try {
      const factors = await sharedParkingAdapter.toAuxiliaryRiskFactors();
      res.json(factors);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.get("/", (_req, res) => {
    res.redirect("/tourist.html");
  });

  return app;
}


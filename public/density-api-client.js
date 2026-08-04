/**
 * 브라우저용 Density API 클라이언트 (엔진 재구현 없음, HTTP만 소비)
 * window.DensityApi / DensityApiClient
 */
(function (global) {
  const CONNECT_FAIL = "밀도 분석 서비스 연결 실패";
  const DEFAULT_ZONE = "GWANGALLI-ZONE-CENTER";

  function normalizeBase(url) {
    if (!url) return "";
    return String(url).replace(/\/$/, "");
  }

  function resolveDefaultBase() {
    if (typeof global.__DENSITY_API_BASE_URL__ === "string") {
      return normalizeBase(global.__DENSITY_API_BASE_URL__);
    }
    try {
      const meta = document.querySelector('meta[name="density-api-base"]');
      if (meta && meta.content) return normalizeBase(meta.content);
    } catch (_) {
      /* ignore */
    }
    // 동일 오리진(이 서버의 public UI)이면 상대 경로
    return "";
  }

  function visionOutputUrl(baseUrl, relativePath) {
    if (!relativePath) return null;
    const stripped = String(relativePath)
      .replace(/^\/+/, "")
      .replace(/^vision\/output\//, "");
    const root = normalizeBase(baseUrl);
    return `${root}/vision-output/${stripped}`;
  }

  class DensityApiError extends Error {
    constructor(message, status, body) {
      super(message);
      this.name = "DensityApiError";
      this.status = status ?? null;
      this.body = body;
    }
  }

  class DensityApiClient {
    constructor(options) {
      options = options || {};
      this.baseUrl = normalizeBase(
        options.baseUrl != null ? options.baseUrl : resolveDefaultBase(),
      );
      this.manualTimeoutMs = options.manualTimeoutMs ?? 10_000;
      this.visionTimeoutMs = options.visionTimeoutMs ?? 180_000;
      this.defaultTimeoutMs = options.defaultTimeoutMs ?? 10_000;
      this.fetchImpl = options.fetchImpl || fetch.bind(global);
    }

    visionAssetUrl(relativePath) {
      return visionOutputUrl(this.baseUrl, relativePath);
    }

    health() {
      return this._request("/api/health", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    analyzeManual(body) {
      body = body || {};
      return this._request("/api/analyze/manual", {
        method: "POST",
        timeoutMs: this.manualTimeoutMs,
        body: {
          zoneId: body.zoneId || DEFAULT_ZONE,
          detectedPeople: body.detectedPeople,
          measuredAt: body.measuredAt,
          notify: body.notify ?? false,
          skipHysteresis: body.skipHysteresis,
          effectiveAreaSquareMeters: body.effectiveAreaSquareMeters,
          confidence: body.confidence,
          dataSource: body.dataSource,
          isTestData: body.isTestData,
        },
      });
    }

    getResults() {
      return this._request("/api/results", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    getTouristZones() {
      return this._request("/api/tourist/zones", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    getTouristBeach() {
      return this._request("/api/tourist/beach", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    getAdminZones() {
      return this._request("/api/admin/zones", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    analyzeVision(body) {
      body = body || {};
      return this._request("/api/analyze/vision", {
        method: "POST",
        timeoutMs: this.visionTimeoutMs,
        body: {
          imagePath: body.imagePath,
          zoneId: body.zoneId || DEFAULT_ZONE,
          skipHysteresis: body.skipHysteresis ?? true,
          notify: body.notify ?? false,
          calibrationPath: body.calibrationPath,
          useHomographyArea: body.useHomographyArea,
        },
      });
    }

    getRealtimeVision() {
      return this._request("/api/vision/realtime", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    getRealtimeVisionStatus() {
      return this._request("/api/vision/realtime/status", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    getRealtimeVisionModel() {
      return this._request("/api/vision/realtime/model", {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    /** 실시간 status 폴링. 반환: { stop() } */
    startRealtimePolling(onUpdate, options) {
      options = options || {};
      const intervalMs = options.intervalMs ?? 2000;
      let stopped = false;
      let timer = null;
      const self = this;
      const tick = function () {
        if (stopped) return;
        self
          .getRealtimeVisionStatus()
          .then(function (status) {
            if (!stopped && typeof onUpdate === "function") onUpdate(status);
          })
          .catch(function (err) {
            if (!stopped && typeof options.onError === "function") {
              options.onError(err);
            }
          })
          .then(function () {
            if (!stopped) timer = setTimeout(tick, intervalMs);
          });
      };
      tick();
      return {
        stop: function () {
          stopped = true;
          if (timer) clearTimeout(timer);
        },
      };
    }

    getWaveguardDashboard(params) {
      params = params || {};
      const q = new URLSearchParams();
      if (params.date) q.set("date", params.date);
      if (params.telecomRefresh != null) {
        q.set("telecomRefresh", String(params.telecomRefresh));
      }
      const qs = q.toString();
      return this._request(`/api/waveguard/dashboard${qs ? `?${qs}` : ""}`, {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      });
    }

    async _request(path, opts) {
      const url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), opts.timeoutMs);
      try {
        const res = await this.fetchImpl(url, {
          method: opts.method,
          headers:
            opts.body !== undefined
              ? { "Content-Type": "application/json" }
              : undefined,
          body:
            opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
          signal: controller.signal,
        });
        let data = null;
        const text = await res.text();
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (_) {
            data = text;
          }
        }
        if (!res.ok) {
          const errMsg =
            data && typeof data === "object" && typeof data.error === "string"
              ? data.error
              : `${CONNECT_FAIL} (HTTP ${res.status})`;
          throw new DensityApiError(errMsg, res.status, data);
        }
        return data;
      } catch (err) {
        if (err instanceof DensityApiError) throw err;
        if (err && err.name === "AbortError") {
          throw new DensityApiError(
            `${CONNECT_FAIL} (타임아웃 ${opts.timeoutMs}ms)`,
            null,
          );
        }
        throw new DensityApiError(
          `${CONNECT_FAIL}: ${err && err.message ? err.message : String(err)}`,
          null,
        );
      } finally {
        clearTimeout(timer);
      }
    }
  }

  global.DensityApiClient = DensityApiClient;
  global.DensityApi = {
    Client: DensityApiClient,
    create: (opts) => new DensityApiClient(opts),
    visionOutputUrl,
    DEFAULT_ZONE,
    CONNECT_FAIL,
    /** 고정 문구 — 변경 금지 */
    TOURIST_DANGER_MESSAGE:
      "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요.",
    MANAGER_DANGER_MESSAGE:
      "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요.",
  };
})(window);

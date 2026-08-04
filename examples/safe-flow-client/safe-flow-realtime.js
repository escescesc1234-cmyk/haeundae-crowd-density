/**
 * Safe Flow 실시간 AI — 다른 프로젝트에 복사해 쓰는 최소 클라이언트
 *
 * 사용:
 *   <script src="./safe-flow-realtime.js"></script>
 *   const sf = SafeFlowRealtime.create({ baseUrl: "http://localhost:3780" });
 *   const meta = await sf.getMeta();
 *   img.src = meta.streamUrl; // SAHI-256 실시간
 *   // 9:16 + object-fit:contain (잘림 금지)
 *   const { stop } = sf.pollStatus((st) => console.log(st.estimatedTotal, st.sahi256));
 *
 * 서버 기동(그 PC):
 *   npm run dev && npm run vision:realtime
 */
(function (global) {
  const CONNECT_FAIL = "밀도 분석 서비스 연결 실패";

  function normalize(url) {
    return String(url || "").replace(/\/$/, "");
  }

  async function requestJson(baseUrl, path, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || 8000);
    try {
      const res = await fetch(normalize(baseUrl) + path, {
        signal: controller.signal,
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const msg =
          data && typeof data.error === "string"
            ? data.error
            : CONNECT_FAIL + " (HTTP " + res.status + ")";
        const err = new Error(msg);
        err.status = res.status;
        err.body = data;
        throw err;
      }
      return data;
    } catch (e) {
      if (e && e.name === "AbortError") {
        throw new Error(CONNECT_FAIL + " (타임아웃)");
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  function create(options) {
    options = options || {};
    const baseUrl = normalize(
      options.baseUrl ||
        (typeof global.__DENSITY_API_BASE_URL__ === "string"
          ? global.__DENSITY_API_BASE_URL__
          : "http://localhost:3780"),
    );

    return {
      baseUrl,
      health: () => requestJson(baseUrl, "/api/health", 10000),
      getMeta: () => requestJson(baseUrl, "/api/vision/realtime", 10000),
      getStatus: () =>
        requestJson(baseUrl, "/api/vision/realtime/status", 8000),
      /** 권장: 스트림+숫자를 한 번에 (부분 탐지 OK) */
      getMonitor: () =>
        requestJson(baseUrl, "/api/vision/realtime/monitor", 12000),
      getModel: () =>
        requestJson(baseUrl, "/api/vision/realtime/model", 8000),
      analyzeManual: (body) =>
        fetch(baseUrl + "/api/analyze/manual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            zoneId: (body && body.zoneId) || "GWANGALLI-ZONE-CENTER",
            detectedPeople: body.detectedPeople,
            measuredAt: body.measuredAt || new Date().toISOString(),
            notify: false,
          }),
        }).then(async (r) => {
          const data = await r.json();
          if (!r.ok) throw new Error(data.error || CONNECT_FAIL);
          return data;
        }),
      pollStatus: function (onUpdate, opts) {
        opts = opts || {};
        const intervalMs = opts.intervalMs || 2000;
        let stopped = false;
        let timer = null;
        const self = this;
        const tick = function () {
          if (stopped) return;
          self
            .getMonitor()
            .then(function (st) {
              if (!stopped) onUpdate(st);
            })
            .catch(function (err) {
              if (!stopped && opts.onError) opts.onError(err);
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
      },
    };
  }

  global.SafeFlowRealtime = {
    create,
    CONNECT_FAIL,
    TOURIST_DANGER_MESSAGE:
      "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요.",
    MANAGER_DANGER_MESSAGE:
      "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요.",
  };
})(typeof window !== "undefined" ? window : globalThis);

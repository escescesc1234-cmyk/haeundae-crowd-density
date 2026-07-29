/**
 * WaveGuard 관광객·관리자 공통 렌더러
 * - 대시보드: /api/waveguard/dashboard
 * - 구역 밀도·비전: DensityApiClient (HTTP만 소비, 엔진 재구현 없음)
 */
(function (global) {
  const COLORS = {
    안전: "#22b14c",
    혼잡: "#ff9f2e",
    주의: "#ff9f2e",
    위험: "#f44336",
    데이터없음: "#c3ccd6",
  };

  const DEFAULT_ZONE = "GWANGALLI-ZONE-CENTER";
  const SAMPLE_VISION =
    "vision/input/screenshots/01_wide_full_beach.png";

  function createDensityClient() {
    if (!global.DensityApiClient) return null;
    return new global.DensityApiClient();
  }

  function weatherIcon(sky) {
    if (!sky) return "☀️";
    if (sky.includes("비") || sky.includes("소나기")) return "🌧️";
    if (sky.includes("눈")) return "🌨️";
    if (sky.includes("흐림") || sky.includes("구름")) return "☁️";
    return "☀️";
  }

  function formatVisitors(n) {
    if (n == null || Number.isNaN(n)) return "예상 방문자 --";
    return `예상 방문자 ${Number(n).toLocaleString("ko-KR")}명`;
  }

  function polar(cx, cy, r, deg) {
    const rad = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
  }

  function renderStats(dash) {
    document.getElementById("statDate").textContent = dash.dateLabel;
    document.getElementById("statWeatherIcon").textContent = weatherIcon(
      dash.weather.skyCondition,
    );
    document.getElementById("statWeather").textContent =
      `${dash.weather.skyCondition} ${dash.weather.temperatureCelsius}°C`;
    document.getElementById("statVisitors").textContent = formatVisitors(
      dash.expectedVisitors,
    );
    const beach = document.getElementById("beachName");
    if (beach) beach.textContent = dash.beachName;
  }

  function renderPredictions(dash, role) {
    const copy = role === "admin" ? dash.copy.admin : dash.copy.tourist;
    const d = dash.distribution;
    const rows = [
      { key: "안전", cls: "safe", pct: d.안전, msg: copy.안전 },
      { key: "주의", cls: "warn", pct: d.주의, msg: copy.주의 },
      { key: "위험", cls: "danger", pct: d.위험, msg: copy.위험 },
    ];
    document.getElementById("predList").innerHTML = rows
      .map(
        (r) => `
      <div class="pred-row">
        <div class="pred-badge ${r.cls}">${r.key}</div>
        <div class="pred-bar-wrap">
          <div class="pred-meta">
            <span class="pred-pct">${r.pct}%</span>
          </div>
          <div class="pred-track"><div class="pred-fill ${r.cls}" style="width:${r.pct}%"></div></div>
          <p class="pred-msg">${r.msg}</p>
        </div>
      </div>`,
      )
      .join("");
  }

  function mapDisplayLevel(dash) {
    if (dash.map && dash.map.source === "sk") return dash.map.displayLevel;
    return dash.currentRisk.displayLevel;
  }

  function renderSkMeta(dash) {
    const hint = document.querySelector(".map-hint");
    if (!hint || !dash.telecom) return;
    const t = dash.telecom;
    if (t.apiStatus === "connected" || t.apiStatus === "cached") {
      const time = t.measuredAt
        ? new Date(t.measuredAt).toLocaleTimeString("ko-KR", {
            hour: "2-digit",
            minute: "2-digit",
          })
        : "";
      hint.textContent = `SK 장소 혼잡도: ${t.congestionLabel || "—"}${t.poiName ? ` · ${t.poiName}` : ""}${time ? ` · ${time} 기준` : ""}`;
    }
  }

  function renderRiskCard(dash) {
    const card = document.getElementById("riskCard");
    if (!card) return;
    const level = mapDisplayLevel(dash);
    document.getElementById("riskPct").textContent = dash.currentRisk.percent;
    const lvl = document.getElementById("riskLevel");
    lvl.textContent = level;
    lvl.style.color = COLORS[level] || COLORS.데이터없음;
    document.getElementById("riskMsg").textContent =
      dash.copy.adminGuide[level] || dash.copy.adminGuide.데이터없음;
  }

  function dangerPinHtml(level, cx, cy) {
    if (level !== "위험") return "";
    return `<g transform="translate(${cx - 28},${cy + 8})">
      <circle cx="28" cy="22" r="20" fill="#f44336" stroke="#fff" stroke-width="2.5"/>
      <text x="28" y="18" text-anchor="middle" class="danger-pin" font-size="9">위험</text>
      <text x="28" y="30" text-anchor="middle" font-size="8" fill="#fff" font-weight="700">구역</text>
      <title>위험 구역</title>
    </g>`;
  }

  function renderMap(dash, role) {
    const g = document.getElementById("zoneWedges");
    if (!g) return;
    const cx = 180;
    const cy = 118;
    const r = 108;
    const startDeg = 200;
    const endDeg = -10;

    if (role === "admin" && dash.zones.length > 1) {
      const zones = dash.zones.slice(0, 7);
      const span = (startDeg - endDeg + 360) % 360 || 170;
      const slice = span / zones.length;
      const skUniform = dash.map && dash.map.source === "sk";
      const beachLevel = mapDisplayLevel(dash);
      let html = "";
      zones.forEach((z, i) => {
        const a0 = startDeg - i * slice;
        const a1 = startDeg - (i + 1) * slice;
        const [x0, y0] = polar(cx, cy, r, a0);
        const [x1, y1] = polar(cx, cy, r, a1);
        const wedgeLevel = skUniform ? beachLevel : z.displayLevel;
        const fill = COLORS[wedgeLevel] || COLORS.데이터없음;
        html += `<path d="M${cx},${cy} L${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)} Z"
          fill="${fill}" stroke="#fff" stroke-width="2" opacity="0.88">
          <title>${z.zoneName}: ${skUniform ? `${beachLevel} (SK)` : z.displayLevel}</title></path>`;
      });
      g.innerHTML = html;
    } else {
      const level = mapDisplayLevel(dash);
      const fill = COLORS[level] || COLORS.데이터없음;
      const [x0, y0] = polar(cx, cy, r, startDeg);
      const [x1, y1] = polar(cx, cy, r, endDeg);
      const largeArc = Math.abs(startDeg - endDeg) > 180 ? 1 : 0;
      g.innerHTML = `
        <path d="M${cx},${cy} L${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 ${largeArc} 1 ${x1.toFixed(1)},${y1.toFixed(1)} Z"
          fill="${fill}" stroke="#ffffff" stroke-width="2.5" opacity="0.9">
          <title>${dash.beachName}: ${level}</title>
        </path>${dangerPinHtml(level, cx, cy)}`;
    }

    const list = document.getElementById("zoneList");
    if (!list) return;
    if (role === "admin") {
      list.innerHTML = dash.zones
        .map(
          (z) => `
        <div class="zone-row">
          <span class="zone-dot" style="background:${COLORS[z.displayLevel] || COLORS.데이터없음}"></span>
          <span><strong>${z.zoneName}</strong> · ${z.displayLevel}
            ${z.adjustedDensity != null ? ` · ${z.adjustedDensity}명/㎡` : ""}</span>
        </div>`,
        )
        .join("");
    } else {
      const level = mapDisplayLevel(dash);
      list.innerHTML = `
        <div class="zone-row">
          <span class="zone-dot" style="background:${COLORS[level] || COLORS.데이터없음}"></span>
          <span><strong>${dash.beachName}</strong> · ${level}<br/>
          ${dash.copy.touristGuide[level] || ""}</span>
        </div>`;
    }
  }

  const DASHBOARD_MS = 60 * 1000;
  const TELECOM_REFRESH_MS = 10 * 60 * 1000;
  let lastTelecomRefresh = 0;
  let lastNotifiedLevel = null;
  let lastVisionAlerts = null;

  function setDensityStatus(text) {
    const el = document.getElementById("densityStatus");
    if (el) el.textContent = text || "";
  }

  function showDensityAlert(message, isDanger) {
    const el = document.getElementById("densityAlert");
    if (!el) return;
    if (!message) {
      el.textContent = "";
      el.classList.remove("show", "danger");
      return;
    }
    el.textContent = message;
    el.classList.add("show");
    el.classList.toggle("danger", Boolean(isDanger));
  }

  function renderVisionMaps(client, vision) {
    const wrap = document.getElementById("visionMaps");
    if (!wrap) return;
    if (!vision) {
      wrap.hidden = true;
      wrap.innerHTML = "";
      return;
    }
    const safety = client.visionAssetUrl(vision.safetyMapRelativePath);
    const heat = client.visionAssetUrl(vision.heatmapRelativePath);
    const parts = [];
    if (safety) {
      parts.push(
        `<figure><img src="${safety}" alt="비전 안전지도" /><figcaption>안전지도</figcaption></figure>`,
      );
    }
    if (heat) {
      parts.push(
        `<figure><img src="${heat}" alt="열지도" /><figcaption>열지도 (app_bridge)</figcaption></figure>`,
      );
    }
    if (!parts.length) {
      wrap.hidden = true;
      wrap.innerHTML = "";
      return;
    }
    wrap.hidden = false;
    wrap.innerHTML = parts.join("");
  }

  function formatDensityLine(z, role) {
    const people =
      z.detectedPeople != null ? `${z.detectedPeople}명` : "인원 —";
    const dens =
      z.adjustedDensity != null
        ? `${Number(z.adjustedDensity).toFixed(2)}명/㎡`
        : z.rawDensity != null
          ? `${Number(z.rawDensity).toFixed(2)}명/㎡`
          : "밀도 —";
    const risk = z.riskLevel || "데이터없음";
    if (role === "admin") {
      return `<strong>${z.zoneName || z.zoneId}</strong> · 등급 <span style="color:${COLORS[risk] || COLORS.데이터없음}">${risk}</span> · ${people} · ${dens}`;
    }
    return `<strong>${z.zoneName || z.zoneId}</strong> · 등급 <span style="color:${COLORS[risk] || COLORS.데이터없음}">${risk}</span>`;
  }

  async function loadDensityPanel(role, opts) {
    opts = opts || {};
    const meta = document.getElementById("densityMeta");
    const client = createDensityClient();
    if (!meta) return;
    if (!client) {
      meta.textContent = "밀도 분석 서비스 연결 실패 (클라이언트 미로드)";
      return;
    }
    try {
      await client.health();
      let results = await client.getResults();
      if ((!results || !results.length) && opts.seedIfEmpty) {
        setDensityStatus("결과 없음 → 샘플 수동 분석 실행…");
        await client.analyzeManual({
          zoneId: DEFAULT_ZONE,
          detectedPeople: 800,
          measuredAt: new Date().toISOString(),
          notify: false,
        });
        results = await client.getResults();
      }

      const byId = Object.create(null);
      (results || []).forEach((r) => {
        byId[r.zoneId] = r;
      });

      let zones;
      if (role === "admin") {
        zones = await client.getAdminZones();
      } else {
        const tourist = await client.getTouristZones();
        zones = (tourist || []).map((z) => {
          const full = byId[z.zoneId] || {};
          return {
            ...z,
            detectedPeople:
              full.detectedPeople != null ? full.detectedPeople : null,
            adjustedDensity:
              full.adjustedDensity != null ? full.adjustedDensity : null,
            rawDensity: full.rawDensity != null ? full.rawDensity : null,
          };
        });
      }

      if (!zones || !zones.length) {
        meta.textContent =
          "아직 분석 결과가 없습니다. 「샘플 수동 분석」을 눌러 주세요.";
        showDensityAlert(null);
      } else {
        meta.innerHTML = zones
          .map((z) => {
            if (role === "tourist") {
              const people =
                z.detectedPeople != null ? `${z.detectedPeople}명` : "인원 —";
              const dens =
                z.adjustedDensity != null
                  ? `${Number(z.adjustedDensity).toFixed(2)}명/㎡`
                  : "밀도 —";
              const risk = z.riskLevel || "데이터없음";
              return `<strong>${z.zoneName || z.zoneId}</strong> · 등급 <span style="color:${COLORS[risk] || COLORS.데이터없음}">${risk}</span> · ${people} · ${dens}`;
            }
            return formatDensityLine(z, role);
          })
          .join("<br/>");
        if (lastVisionAlerts && lastVisionAlerts.hasDanger) {
          const msg =
            role === "admin"
              ? lastVisionAlerts.managerMessage ||
                global.DensityApi.MANAGER_DANGER_MESSAGE
              : lastVisionAlerts.touristMessage ||
                global.DensityApi.TOURIST_DANGER_MESSAGE;
          showDensityAlert(msg, true);
        } else {
          showDensityAlert(null);
        }
      }
      setDensityStatus(
        `밀도 API 연결됨 · 구역 ${zones.length} · ${new Date().toLocaleTimeString("ko-KR")}`,
      );
    } catch (e) {
      const msg =
        e && e.message && String(e.message).includes("밀도 분석 서비스")
          ? e.message
          : `밀도 분석 서비스 연결 실패: ${e.message || e}`;
      meta.textContent = msg;
      showDensityAlert(msg, true);
      setDensityStatus("");
      const banner = document.getElementById("errorBanner");
      if (banner) {
        banner.textContent = msg;
        banner.style.display = "block";
      }
    }
  }

  async function runSampleManual(role) {
    const client = createDensityClient();
    if (!client) return;
    setDensityStatus("수동 분석 중…");
    try {
      const result = await client.analyzeManual({
        zoneId: DEFAULT_ZONE,
        detectedPeople: 800,
        measuredAt: new Date().toISOString(),
        notify: false,
      });
      setDensityStatus(
        `수동 분석 완료 · ${result.zoneName || result.zoneId} · ${result.riskLevel}`,
      );
      await loadDensityPanel(role, { seedIfEmpty: false });
      await load(role, false);
    } catch (e) {
      setDensityStatus("");
      showDensityAlert(e.message || "밀도 분석 서비스 연결 실패", true);
    }
  }

  async function runVisionAnalyze(role) {
    const client = createDensityClient();
    if (!client) return;
    const btn = document.getElementById("btnVisionAnalyze");
    if (btn) btn.disabled = true;
    setDensityStatus("비전 분석 중 (최대 약 3분)…");
    try {
      const res = await client.analyzeVision({
        imagePath: SAMPLE_VISION,
        zoneId: DEFAULT_ZONE,
        skipHysteresis: true,
        notify: false,
      });
      lastVisionAlerts = res.alerts || null;
      renderVisionMaps(client, res.vision);
      if (res.alerts && res.alerts.hasDanger) {
        const msg =
          role === "admin"
            ? res.alerts.managerMessage ||
              global.DensityApi.MANAGER_DANGER_MESSAGE
            : res.alerts.touristMessage ||
              global.DensityApi.TOURIST_DANGER_MESSAGE;
        showDensityAlert(msg, true);
      } else {
        showDensityAlert(null);
      }
      const people =
        res.analysis && res.analysis.detectedPeople != null
          ? res.analysis.detectedPeople
          : res.vision && res.vision.roiPersonCount;
      setDensityStatus(
        `비전 완료 · 등급 ${res.analysis?.riskLevel ?? "—"} · 인원 ${people ?? "—"} · 위험격자 ${res.alerts?.dangerCellCount ?? 0}`,
      );
      await loadDensityPanel(role, { seedIfEmpty: false });
      await load(role, false);
    } catch (e) {
      setDensityStatus("");
      showDensityAlert(e.message || "밀도 분석 서비스 연결 실패", true);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function maybeNotifyRisk(role, level) {
    const S = global.WaveGuardSettings;
    if (!S || !level || level === lastNotifiedLevel) return;
    const prefs = S.load().notifications;
    if (level === "위험" && !prefs.danger) return;
    if ((level === "주의" || level === "혼잡") && !prefs.congestion) return;
    if (level !== "위험" && level !== "주의" && level !== "혼잡") return;
    if (!prefs.push) return;
    if (!("Notification" in window) || Notification.permission !== "granted")
      return;
    lastNotifiedLevel = level;
    const title = role === "admin" ? "WaveGuard 관리자" : "WaveGuard";
    new Notification(title, {
      body:
        level === "위험"
          ? "위험 등급이 감지되었습니다. 화면에서 상세를 확인하세요."
          : "주의(혼잡) 등급입니다. 주변 상황을 확인해 주세요.",
    });
  }

  async function load(role, forceTelecomRefresh) {
    const banner = document.getElementById("errorBanner");
    const client = createDensityClient();
    try {
      const telecomRefresh =
        forceTelecomRefresh === true ||
        Date.now() - lastTelecomRefresh >= TELECOM_REFRESH_MS;
      if (telecomRefresh) lastTelecomRefresh = Date.now();

      let dash;
      if (client) {
        dash = await client.getWaveguardDashboard({
          date: new Date().toISOString().slice(0, 10),
          telecomRefresh,
        });
      } else {
        const res = await fetch(
          `/api/waveguard/dashboard?date=${new Date().toISOString().slice(0, 10)}&telecomRefresh=${telecomRefresh}`,
        );
        dash = await res.json();
        if (!res.ok || dash.error)
          throw new Error(dash.error || `HTTP ${res.status}`);
      }
      if (dash && dash.error) throw new Error(dash.error);

      renderStats(dash);
      renderPredictions(dash, role);
      renderRiskCard(dash);
      renderMap(dash, role);
      renderSkMeta(dash);
      maybeNotifyRisk(role, mapDisplayLevel(dash));
      banner.style.display = "none";
    } catch (e) {
      const msg =
        e && e.message && String(e.message).includes("밀도 분석 서비스")
          ? e.message
          : `데이터 로드 실패: ${e.message}`;
      banner.textContent = msg;
      banner.style.display = "block";
    }
  }

  function bindDensityActions(role) {
    const refresh = document.getElementById("btnDensityRefresh");
    const sample = document.getElementById("btnDensitySample");
    const vision = document.getElementById("btnVisionAnalyze");
    if (refresh) {
      refresh.addEventListener("click", () =>
        loadDensityPanel(role, { seedIfEmpty: false }),
      );
    }
    if (sample) {
      sample.addEventListener("click", () => runSampleManual(role));
    }
    if (vision) {
      vision.addEventListener("click", () => runVisionAnalyze(role));
    }
  }

  function mount(role) {
    const S = global.WaveGuardSettings;
    if (S) {
      S.applyAccessibility();
      if (role === "admin" || role === "tourist") S.login(role);
      const name = S.load().account.displayName;
      const logo = document.querySelector(".app-header .logo");
      if (logo && name) {
        const base = role === "admin" ? "WaveGuard 관리자" : "WaveGuard";
        logo.innerHTML = `<span class="mark" aria-hidden="true">😊</span> ${base} · ${name}`;
      }
    }

    const beachSelect = document.getElementById("beachSelect");
    const list = document.getElementById("zoneList");
    if (beachSelect && list) {
      beachSelect.addEventListener("click", () => {
        const open = list.classList.toggle("open");
        beachSelect.setAttribute("aria-expanded", open ? "true" : "false");
        const chev = beachSelect.querySelector(".chev");
        if (chev) chev.textContent = open ? "▴" : "▾";
      });
    }

    const cta = document.getElementById("mapCta");
    if (cta && role === "tourist") {
      cta.addEventListener("click", (e) => {
        e.preventDefault();
        if (!list) return;
        const open = list.classList.toggle("open");
        if (beachSelect) {
          beachSelect.setAttribute("aria-expanded", open ? "true" : "false");
          const chev = beachSelect.querySelector(".chev");
          if (chev) chev.textContent = open ? "▴" : "▾";
        }
        cta.textContent = open ? "접기 ∧" : "지도 크게 보기 >";
      });
    }

    load(role, true);
    setInterval(() => load(role, false), DASHBOARD_MS);
    if (role === "admin") {
      bindDensityActions(role);
      loadDensityPanel(role, { seedIfEmpty: true });
      setInterval(
        () => loadDensityPanel(role, { seedIfEmpty: false }),
        DASHBOARD_MS,
      );
    }
  }

  global.WaveGuard = { mount, loadDensityPanel };
})(window);

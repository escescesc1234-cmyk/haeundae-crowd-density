/**
 * WaveGuard 관광객·관리자 공통 렌더러
 * 데이터는 /api/waveguard/dashboard 한곳에서만 가져온다.
 */
(function (global) {
  const COLORS = {
    안전: "#22b14c",
    주의: "#ff9f2e",
    위험: "#f44336",
    데이터없음: "#c3ccd6",
  };

  function weatherIcon(sky) {
    if (!sky) return "☀️";
    if (sky.includes("비") || sky.includes("소나기")) return "🌧️";
    if (sky.includes("눈")) return "🌨️";
    if (sky.includes("흐림") || sky.includes("구름")) return "☁️";
    return "☀️";
  }

  function formatVisitors(n) {
    if (n == null || Number.isNaN(n)) return "--";
    return `${Number(n).toLocaleString("ko-KR")}명`;
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
      hint.textContent = `SK 장소 혼잡도: ${t.congestionLabel || "—"}${t.poiName ? ` · ${t.poiName}` : ""}${time ? ` · ${time} 기준` : ""} (${t.refreshIntervalMinutes}분마다 갱신)`;
    } else {
      hint.textContent =
        t.apiStatus === "quota_exceeded"
          ? "SK 혼잡도: 호출 한도 초과 — 캐시·실측 데이터를 표시합니다."
          : `SK 혼잡도: ${t.message || t.apiStatus}`;
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

  function renderMap(dash, role) {
    const g = document.getElementById("zoneWedges");
    if (!g) return;
    const cx = 180;
    const cy = 88;
    const r = 118;
    const startDeg = 175;
    const endDeg = 5;

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
          fill="${fill}" stroke="#fff" stroke-width="2" opacity="0.9">
          <title>${z.zoneName}: ${skUniform ? `${beachLevel} (SK)` : z.displayLevel}</title></path>`;
      });
      g.innerHTML = html;
    } else {
      const level = mapDisplayLevel(dash);
      const fill = COLORS[level] || COLORS.데이터없음;
      const [x0, y0] = polar(cx, cy, r, startDeg);
      const [x1, y1] = polar(cx, cy, r, endDeg);
      const pin =
        level === "위험"
          ? `<g transform="translate(${cx - 18},${cy + 18})">
              <circle cx="18" cy="18" r="16" fill="#f44336" stroke="#fff" stroke-width="2"/>
              <text x="18" y="16" text-anchor="middle" class="danger-pin" font-size="9">위험</text>
              <text x="18" y="26" text-anchor="middle" font-size="8" fill="#fff">구역</text>
            </g>`
          : "";
      g.innerHTML = `
        <path d="M${cx},${cy} L${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)} Z"
          fill="${fill}" stroke="#ffffff" stroke-width="2" opacity="0.92">
          <title>${dash.beachName}: ${level}</title>
        </path>${pin}`;
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
          <span><strong>${dash.beachName} 전체</strong> · ${level}<br/>
          ${dash.copy.touristGuide[level] || ""}</span>
        </div>`;
    }
  }

  const DASHBOARD_MS = 60 * 1000;
  const TELECOM_REFRESH_MS = 10 * 60 * 1000;
  let lastTelecomRefresh = 0;
  let lastNotifiedLevel = null;

  function maybeNotifyRisk(role, level) {
    const S = global.WaveGuardSettings;
    if (!S || !level || level === lastNotifiedLevel) return;
    const prefs = S.load().notifications;
    if (level === "위험" && !prefs.danger) return;
    if (level === "주의" && !prefs.congestion) return;
    if (level !== "위험" && level !== "주의") return;
    if (!prefs.push) return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;
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
    try {
      const telecomRefresh =
        forceTelecomRefresh === true ||
        Date.now() - lastTelecomRefresh >= TELECOM_REFRESH_MS;
      if (telecomRefresh) lastTelecomRefresh = Date.now();
      const res = await fetch(
        `/api/waveguard/dashboard?date=${new Date().toISOString().slice(0, 10)}&telecomRefresh=${telecomRefresh}`,
      );
      const dash = await res.json();
      if (!res.ok || dash.error) throw new Error(dash.error || `HTTP ${res.status}`);

      renderStats(dash);
      renderPredictions(dash, role);
      renderRiskCard(dash);
      renderMap(dash, role);
      renderSkMeta(dash);
      maybeNotifyRisk(role, mapDisplayLevel(dash));
      banner.style.display = "none";
    } catch (e) {
      banner.textContent = `데이터 로드 실패: ${e.message}`;
      banner.style.display = "block";
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
        logo.innerHTML = `<span class="mark">😊</span> ${base} · ${name}`;
      }
    }
    const cta = document.getElementById("mapCta");
    if (cta && role === "tourist") {
      cta.addEventListener("click", (e) => {
        e.preventDefault();
        const list = document.getElementById("zoneList");
        const open = list.classList.toggle("open");
        cta.textContent = open ? "접기 ∧" : "지도 크게 보기 >";
      });
    }
    load(role, true);
    setInterval(() => load(role, false), DASHBOARD_MS);
  }

  global.WaveGuard = { mount };
})(window);

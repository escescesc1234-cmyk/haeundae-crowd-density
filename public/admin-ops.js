/**
 * WaveGuard 운영 도구 — 목업 UI + 기존 API 연동
 */
(function () {
  const LV_TO_DENSITY = { 1: 3.0, 2: 4.0, 3: 5.0, 4: 6.0 };
  const DENSITY_TO_LV = (d) => {
    if (d >= 5.5) return 4;
    if (d >= 4.5) return 3;
    if (d >= 3.5) return 2;
    return 1;
  };

  function todayIsoDate() {
    return new Date().toISOString().slice(0, 10);
  }

  async function parseJsonResponse(res) {
    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      throw new Error(
        res.ok
          ? "서버가 JSON이 아닌 응답을 반환했습니다."
          : `API 오류 (${res.status})`,
      );
    }
    if (!res.ok || json.error) {
      throw new Error(json.error || `API 오류 (${res.status})`);
    }
    return json;
  }

  function setTelecomStatus(status, label) {
    const el = document.getElementById("telecomStatus");
    const text = document.getElementById("telecomStatusText");
    if (!el || !text) return;
    el.classList.remove("warn", "off");
    if (status === "connected" || status === "cached") {
      text.textContent = label || "연결 중";
    } else if (status === "quota_exceeded" || status === "failed") {
      el.classList.add("warn");
      text.textContent = label || "연결 불안정";
    } else {
      el.classList.add("off");
      text.textContent = label || "대기";
    }
  }

  function setQuality(score, warnings) {
    const pct = Math.max(50, Math.min(99, score));
    const ring = document.getElementById("qualityRing");
    const pctEl = document.getElementById("qualityPct");
    const badge = document.getElementById("qualityBadge");
    if (ring) ring.style.setProperty("--pct", `${pct}%`);
    if (pctEl) pctEl.textContent = `${pct}%`;
    if (badge) {
      if (warnings > 2) {
        badge.textContent = "주의";
        badge.className = "ops-pill-good ops-pill-warn";
      } else {
        badge.textContent = "양호";
        badge.className = "ops-pill-good";
      }
    }
  }

  function slotToIndex(timeSlot) {
    const h = parseInt(String(timeSlot).split(":")[0], 10);
    if (Number.isNaN(h)) return -1;
    return h;
  }

  function renderOpsChart(forecastRows) {
    const root = document.getElementById("opsChartBars");
    if (!root) return;
    const hours = [];
    for (let h = 9; h <= 21; h++) hours.push(h);

    const byHour = new Map();
    for (const r of forecastRows) {
      const h = slotToIndex(r.timeSlot);
      if (h < 0) continue;
      const val = byHour.get(h);
      const people = r.expectedPeople ?? 0;
      if (!val || people > val.people) {
        byHour.set(h, { people, slot: r.timeSlot });
      }
    }

    const peopleList = [...byHour.values()].map((v) => v.people);
    const maxPeople = Math.max(...peopleList, 1);
    const scale = (p) => (p / maxPeople) * 5;

    const now = new Date();
    const currentHour = now.getHours();
    let currentIdx = -1;
    let maxVal = -1;
    hours.forEach((h, i) => {
      const row = byHour.get(h);
      const v = row ? scale(row.people) : 0;
      if (h <= currentHour && v >= maxVal) {
        maxVal = v;
        currentIdx = i;
      }
    });
    if (currentIdx < 0) currentIdx = Math.min(5, hours.length - 1);

    let expectedIdx = currentIdx + 1;
    if (expectedIdx >= hours.length) expectedIdx = hours.length - 1;

    root.innerHTML = hours
      .map((h, i) => {
        const row = byHour.get(h);
        const val = row ? scale(row.people) : 0;
        const heightPx = Math.max(4, (val / 5) * 110);
        const label = `${String(h).padStart(2, "0")}:00`;
        let tag = "";
        if (i === currentIdx && val > 0) {
          tag = `<span class="ops-chart-tag current">${val.toFixed(1)} 현재</span>`;
        } else if (i === expectedIdx) {
          const exp = byHour.get(hours[expectedIdx]);
          const ev = exp ? scale(exp.people) : val * 0.85;
          tag = `<span class="ops-chart-tag expected">${ev.toFixed(1)} 예상</span>`;
        }
        return `<div class="ops-bar-col">${tag}<div class="ops-bar" style="height:${heightPx}px" title="${label} ${val.toFixed(1)}"></div><span>${label}</span></div>`;
      })
      .join("");
  }

  async function loadWaveguardTelecom() {
    try {
      const res = await fetch(
        `/api/waveguard/dashboard?date=${todayIsoDate()}&telecomRefresh=false`,
      );
      const dash = await res.json();
      if (!res.ok) throw new Error(dash.error);
      const t = dash.telecom;
      if (t && (t.apiStatus === "connected" || t.apiStatus === "cached")) {
        setTelecomStatus(t.apiStatus, "연결 중");
      } else if (t) {
        setTelecomStatus(t.apiStatus, t.message?.slice(0, 24) || t.apiStatus);
      } else {
        setTelecomStatus("idle", "대기");
      }
    } catch {
      setTelecomStatus("failed", "확인 실패");
    }
  }

  async function runForecast() {
    const params = new URLSearchParams({
      date: todayIsoDate(),
      mode: "live",
      time: "09:00",
      compareYear: "2025",
      useWeather: "true",
      useEventData: "true",
      useTelecom: "false",
    });
    const res = await fetch(`/api/forecast/overview?${params.toString()}`);
    const data = await parseJsonResponse(res);
    const warnings = (data.dataWarnings || []).length;
    setQuality(99 - warnings * 8, warnings);

    const topZone = data.busiestSlots?.[0]?.zoneId;
    const forecasts = Object.values(data.zoneForecasts || {}).flat();
    const rows = forecasts.filter((f) => !topZone || f.zoneId === topZone);
    renderOpsChart(rows.length ? rows : forecasts);

    return data;
  }

  async function loadZones() {
    const res = await fetch("/api/zones");
    const catalog = await res.json();
    const zone = catalog.zones[0];
    const hidden = document.getElementById("zoneId");
    const display = document.getElementById("zoneDisplay");
    if (zone && hidden) {
      hidden.value = zone.zoneId;
      if (display) display.value = zone.zoneName;
    }
  }

  async function loadThresholds() {
    const t = await (await fetch("/api/thresholds")).json();
    const cong = document.getElementById("tCongestionLv");
    const risk = document.getElementById("tRiskLv");
    if (cong) cong.value = String(DENSITY_TO_LV(t.congestionStartDensity));
    if (risk) risk.value = String(DENSITY_TO_LV(t.highRiskDensity));
  }

  function initNotifyToggle() {
    const toggle = document.getElementById("publicNotifyToggle");
    if (!toggle) return;
    const S = window.WaveGuardSettings;
    if (S) {
      const s = S.load();
      toggle.checked = Boolean(s.notifications.publicCitizen);
      toggle.addEventListener("change", () => {
        S.update({ notifications: { publicCitizen: toggle.checked } });
      });
      return;
    }
    const saved = localStorage.getItem("wgPublicNotify");
    toggle.checked = saved !== "false";
    toggle.addEventListener("change", () => {
      localStorage.setItem("wgPublicNotify", toggle.checked ? "true" : "false");
    });
  }

  async function refreshAdvancedZones() {
    const root = document.getElementById("advancedPanel");
    if (!root) return;
    const results = await (await fetch("/api/admin/zones")).json();
    root.innerHTML = `
      <p class="ops-chart-sub">구역별 실측·알림·예측 테스트</p>
      <div class="actions">
        <button type="button" id="btnRefreshZones">구역 새로고침</button>
        <button type="button" id="btnForecastAdv">예측 새로고침</button>
        <button type="button" id="btnTelecomTest">SK API 테스트</button>
        <button type="button" id="btnLoadAlerts">알림 이벤트</button>
        <button type="button" class="warn" id="btnSeedDemo">데모 투입</button>
      </div>
      <p class="meta" id="telecomTestMsg"></p>
      <div id="adminCards" style="display:grid;gap:0.65rem;margin-top:0.5rem"></div>
      <div id="alertEvents" style="margin-top:0.75rem;font-size:0.75rem;overflow:auto"></div>
    `;

    const cards = document.getElementById("adminCards");
    cards.innerHTML = results
      .map(
        (z) => `
      <article style="border:1px solid #eef2f6;border-radius:10px;padding:0.55rem">
        <strong>${z.zoneName}</strong> · ${z.riskLevel}
        <p class="meta">밀도 ${z.adjustedDensity ?? "-"} · 인원 ${z.detectedPeople ?? "-"}</p>
      </article>`,
      )
      .join("");

    document.getElementById("btnRefreshZones").onclick = refreshAdvancedZones;
    document.getElementById("btnForecastAdv").onclick = () =>
      runForecast().catch((e) => alert(e.message));
    document.getElementById("btnTelecomTest").onclick = async () => {
      const msg = document.getElementById("telecomTestMsg");
      msg.textContent = "SK API 호출 중…";
      try {
        const res = await fetch("/api/population/telecom/test", {
          method: "POST",
        });
        const data = await parseJsonResponse(res);
        msg.textContent = `${data.apiStatus} — ${data.message}`;
        loadWaveguardTelecom();
      } catch (e) {
        msg.textContent = e.message;
      }
    };
    document.getElementById("btnLoadAlerts").onclick = loadAlertEvents;
    document.getElementById("btnSeedDemo").onclick = runSeedDemo;
  }

  async function loadAlertEvents() {
    const root = document.getElementById("alertEvents");
    if (!root) return;
    const events = await (await fetch("/api/notifications/events")).json();
    if (!events.length) {
      root.innerHTML = "<p class='meta'>알림 이벤트 없음</p>";
      return;
    }
    root.innerHTML = `<table style="width:100%;font-size:0.7rem"><thead><tr>
      <th>구역</th><th>등급</th><th>상태</th><th>시각</th>
    </tr></thead><tbody>${events
      .slice(0, 15)
      .map(
        (e) => `<tr>
        <td>${e.zoneName}</td>
        <td>${e.currentRiskLevel}</td>
        <td>${e.status}</td>
        <td>${e.createdAt}</td>
      </tr>`,
      )
      .join("")}</tbody></table>`;
  }

  async function runSeedDemo() {
    const demos = [
      {
        zoneId: "GWANGALLI-ZONE-CENTER",
        detectedPeople: 800,
        skipHysteresis: true,
      },
      {
        zoneId: "GWANGALLI-ZONE-CENTER",
        detectedPeople: 1200,
        effectiveAreaSquareMeters: 3100,
        skipHysteresis: true,
      },
    ];
    for (const d of demos) {
      await fetch("/api/analyze/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...d,
          confidence: 0.9,
          dataSource: "test",
          isTestData: true,
          notify: true,
        }),
      });
    }
    await refreshAdvancedZones();
  }

  function bindManualDialog() {
    const dialog = document.getElementById("manualDialog");
    document.getElementById("btnOpenManual").onclick = () => {
      if (dialog.showModal) dialog.showModal();
    };
    document.getElementById("btnCloseManual").onclick = () => dialog.close();

    document.getElementById("btnAnalyze").onclick = async () => {
      try {
        const body = {
          zoneId: document.getElementById("zoneId").value,
          detectedPeople: Number(document.getElementById("people").value),
          confidence: Number(document.getElementById("confidence").value),
          skipHysteresis: document.getElementById("skipHyst").value === "true",
          notify: document.getElementById("notifyOn").value === "true",
          dataSource: "manual",
        };
        const area = document.getElementById("area").value;
        if (area !== "") body.effectiveAreaSquareMeters = Number(area);
        const res = await fetch("/api/analyze/manual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const json = await res.json();
        document.getElementById("lastResult").textContent = JSON.stringify(
          json,
          null,
          2,
        );
        if (!res.ok || json.error) throw new Error(json.error);
        await refreshAdvancedZones();
        await runForecast();
      } catch (e) {
        document.getElementById("lastResult").textContent = e.message;
        alert(`분석 실패: ${e.message}`);
      }
    };
  }

  document.getElementById("btnSaveThresholds").onclick = async () => {
    const congLv = Number(document.getElementById("tCongestionLv").value);
    const riskLv = Number(document.getElementById("tRiskLv").value);
    const msg = document.getElementById("thresholdMsg");
    const res = await fetch("/api/thresholds", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        congestionStartDensity: LV_TO_DENSITY[congLv] ?? 5.0,
        criticalDensity: LV_TO_DENSITY[Math.min(4, congLv + 1)] ?? 5.5,
        highRiskDensity: LV_TO_DENSITY[riskLv] ?? 6.0,
        hysteresisMargin: 0.3,
        changedBy: "admin",
        reason: "운영 도구 UI 기준 반영",
        fieldVerified: false,
        targetZoneIds: "all",
      }),
    });
    const json = await res.json();
    msg.textContent = res.ok
      ? `저장 완료 (${json.record?.id ?? "ok"})`
      : `오류: ${json.error}`;
  };

  document.getElementById("telecomTile").addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    loadWaveguardTelecom();
  });

  document.getElementById("btnChartInfo").onclick = () =>
    runForecast().catch((e) => alert(e.message));

  bindManualDialog();
  initNotifyToggle();
  loadZones();
  loadThresholds();
  refreshAdvancedZones();
  runForecast().catch(() => setQuality(85, 1));
  loadWaveguardTelecom();
  setInterval(loadWaveguardTelecom, 10 * 60 * 1000);
  setInterval(() => runForecast().catch(() => {}), 5 * 60 * 1000);
})();

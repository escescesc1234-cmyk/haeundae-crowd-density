/**
 * WaveGuard 설정 저장소 · 접근성 적용 · 설정 화면 네비게이션
 */
(function (global) {
  const STORAGE_KEY = "waveguard.settings.v1";
  const SESSION_KEY = "waveguard.session.v1";
  const RETURN_KEY = "waveguard.settingsReturn";
  const CONTEXT_KEY = "waveguard.settingsContext";

  const DEFAULTS = {
    role: null,
    account: {
      displayName: "",
      email: "",
      adminId: "mgr-gwangalli-01",
      permission: "구역 관리자",
    },
    notifications: {
      danger: true,
      congestion: true,
      weather: false,
      push: true,
      system: true,
      publicCitizen: true,
    },
    accessibility: {
      fontScale: "md",
      highContrast: false,
      reduceMotion: false,
    },
    privacy: {
      shareAnalytics: false,
      rememberRole: true,
    },
    beach: {
      beachId: "GWANGALLI",
      beachName: "광안리 해수욕장",
    },
  };

  function deepMerge(base, patch) {
    if (!patch || typeof patch !== "object") return structuredClone(base);
    const out = structuredClone(base);
    for (const [k, v] of Object.entries(patch)) {
      if (v && typeof v === "object" && !Array.isArray(v)) {
        out[k] = deepMerge(out[k] || {}, v);
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return structuredClone(DEFAULTS);
      return deepMerge(DEFAULTS, JSON.parse(raw));
    } catch {
      return structuredClone(DEFAULTS);
    }
  }

  function save(next) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    applyAccessibility(next.accessibility);
    syncLegacyNotify(next);
    return next;
  }

  function update(patch) {
    return save(deepMerge(load(), patch));
  }

  function syncLegacyNotify(settings) {
    try {
      localStorage.setItem(
        "wgPublicNotify",
        settings.notifications.publicCitizen ? "true" : "false",
      );
    } catch {
      /* ignore */
    }
  }

  function getSession() {
    try {
      return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function setSession(patch) {
    const next = { ...getSession(), ...patch };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
    return next;
  }

  function isLoggedIn() {
    return Boolean(getSession().loggedIn);
  }

  function login(role) {
    const s = update({ role });
    setSession({ loggedIn: true, role, loggedInAt: new Date().toISOString() });
    return s;
  }

  function logout() {
    setSession({ loggedIn: false, role: null });
    clearSettingsReturn();
    clearSettingsContext();
    const s = load();
    if (!s.privacy.rememberRole) {
      update({ role: null });
    }
  }

  function setSettingsContext(context) {
    try {
      sessionStorage.setItem(CONTEXT_KEY, context);
    } catch {
      /* ignore */
    }
  }

  function getSettingsContext() {
    try {
      return sessionStorage.getItem(CONTEXT_KEY) || "app";
    } catch {
      return "app";
    }
  }

  function clearSettingsContext() {
    try {
      sessionStorage.removeItem(CONTEXT_KEY);
    } catch {
      /* ignore */
    }
  }

  function isSettingsFromLanding() {
    return getSettingsContext() === "landing";
  }

  /** 로그인(역할 선택) 화면에서 연 설정 — 로그아웃 메뉴 숨김 */
  function applySettingsLoginOnlyUI() {
    const hide = isSettingsFromLanding();
    document.querySelectorAll("[data-settings-require-login]").forEach((el) => {
      el.hidden = hide;
      el.style.display = hide ? "none" : "";
    });
    if (hide && location.hash === "#logout") {
      history.replaceState(null, "", location.pathname);
    }
  }

  function setSettingsReturn(url) {
    try {
      sessionStorage.setItem(RETURN_KEY, url);
    } catch {
      /* ignore */
    }
  }

  function clearSettingsReturn() {
    try {
      sessionStorage.removeItem(RETURN_KEY);
    } catch {
      /* ignore */
    }
  }
  function getSettingsReturnUrl(contextRole) {
    try {
      const stored = sessionStorage.getItem(RETURN_KEY);
      if (stored) return stored;
    } catch {
      /* ignore */
    }
    const ses = getSession();
    if (ses.loggedIn && ses.role === "admin" && contextRole === "admin") {
      return "/admin.html";
    }
    if (ses.loggedIn && ses.role === "tourist" && contextRole === "tourist") {
      return "/tourist.html";
    }
    return "/";
  }

  function mountSettingsBack(contextRole) {
    const back = document.querySelector(
      ".app-header a.gear[title='돌아가기']",
    );
    if (!back) return;
    back.href = getSettingsReturnUrl(contextRole);
    back.addEventListener("click", () => {
      clearSettingsReturn();
      clearSettingsContext();
    });
  }

  function goHomeAfterLogout() {
    logout();
    clearSettingsReturn();
    location.href = "/";
  }

  function goRoleSelect() {
    logout();
    clearSettingsReturn();
    location.href = "/";
  }

  function applyAccessibility(a) {
    const prefs = a || load().accessibility;
    const root = document.documentElement;
    root.dataset.fontScale = prefs.fontScale || "md";
    root.dataset.highContrast = prefs.highContrast ? "true" : "false";
    root.dataset.reduceMotion = prefs.reduceMotion ? "true" : "false";
  }

  function fontScaleLabel(scale) {
    if (scale === "lg") return "크게";
    if (scale === "xl") return "더 크게";
    return "보통";
  }

  /** 설정 하위 패널 전환 (hash 기반) */
  function mountPanelNav(root) {
    const menu = root.querySelector("[data-settings-menu]");
    const panels = [...root.querySelectorAll("[data-settings-panel]")];
    if (!menu || !panels.length) return;

    function show(id) {
      const open = Boolean(id);
      menu.hidden = open;
      panels.forEach((p) => {
        p.hidden = p.dataset.settingsPanel !== id;
      });
      if (open) {
        history.replaceState(null, "", `#${id}`);
      } else {
        history.replaceState(null, "", location.pathname);
      }
      window.scrollTo(0, 0);
    }

    root.querySelectorAll("[data-open-panel]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        show(el.getAttribute("data-open-panel"));
      });
    });
    root.querySelectorAll("[data-close-panel]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        show(null);
      });
    });

    const hash = location.hash.replace(/^#/, "");
    if (
      hash &&
      panels.some((p) => p.dataset.settingsPanel === hash) &&
      !(hash === "logout" && isSettingsFromLanding())
    ) {
      show(hash);
    } else {
      show(null);
    }

    window.addEventListener("hashchange", () => {
      const h = location.hash.replace(/^#/, "");
      show(h || null);
    });
  }

  function bindToggle(el, path, onChange) {
    if (!el) return;
    const keys = path.split(".");
    const settings = load();
    let cur = settings;
    for (let i = 0; i < keys.length - 1; i++) cur = cur[keys[i]];
    el.checked = Boolean(cur[keys[keys.length - 1]]);
    el.addEventListener("change", () => {
      const patch = {};
      let node = patch;
      for (let i = 0; i < keys.length - 1; i++) {
        node[keys[i]] = {};
        node = node[keys[i]];
      }
      node[keys[keys.length - 1]] = el.checked;
      const next = update(patch);
      if (onChange) onChange(next);
    });
  }

  function bindSelect(el, path, onChange) {
    if (!el) return;
    const keys = path.split(".");
    const settings = load();
    let cur = settings;
    for (let i = 0; i < keys.length - 1; i++) cur = cur[keys[i]];
    el.value = String(cur[keys[keys.length - 1]] ?? "");
    el.addEventListener("change", () => {
      const patch = {};
      let node = patch;
      for (let i = 0; i < keys.length - 1; i++) {
        node[keys[i]] = {};
        node = node[keys[i]];
      }
      node[keys[keys.length - 1]] = el.value;
      const next = update(patch);
      if (onChange) onChange(next);
    });
  }

  function toast(msg) {
    let el = document.getElementById("settingsToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "settingsToast";
      el.className = "settings-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  applyAccessibility();

  global.WaveGuardSettings = {
    DEFAULTS,
    load,
    save,
    update,
    login,
    logout,
    isLoggedIn,
    getSession,
    setSession,
    applyAccessibility,
    fontScaleLabel,
    mountPanelNav,
    bindToggle,
    bindSelect,
    toast,
    STORAGE_KEY,
    setSettingsReturn,
    clearSettingsReturn,
    setSettingsContext,
    getSettingsContext,
    isSettingsFromLanding,
    applySettingsLoginOnlyUI,
    getSettingsReturnUrl,
    mountSettingsBack,
    goHomeAfterLogout,
    goRoleSelect,
  };
})(window);

# ============================================================
# SK 지오비전 퍼즐 장소 혼잡도 (보조 소스)
# - 실시간 안전지도 UI에 표시
# - YOLO 밀도 판정을 대체하지 않음
# - Free 요금제 보호: 기본 10분 캐시
# ============================================================

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "telecom.gwangalli.json"
ENV_PATH = PROJECT_ROOT / ".env"

CACHE_TTL_SEC = float(os.environ.get("SK_TELECOM_CACHE_MS", str(10 * 60 * 1000))) / 1000.0
QUOTA_COOLDOWN_SEC = float(
    os.environ.get("SK_TELECOM_QUOTA_COOLDOWN_MS", str(6 * 60 * 60 * 1000))
) / 1000.0


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ENV_PATH)


def _level_label(level: Optional[int]) -> str:
    return {1: "여유", 2: "보통", 3: "혼잡", 4: "매우 혼잡"}.get(
        level or -1, "알수없음"
    )


def _pick_number(*vals) -> Optional[float]:
    for v in vals:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _load_poi() -> Optional[dict]:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    places = data.get("places") or []
    return places[0] if places else None


def _parse_payload(json_body: Any, poi: dict) -> dict:
    nested = json_body
    if isinstance(json_body, dict):
        for key in ("contents", "content", "data", "result"):
            if isinstance(json_body.get(key), dict):
                nested = json_body[key]
                break
            if isinstance(json_body.get(key), list) and json_body[key]:
                nested = json_body[key][0]
                break
    if not isinstance(nested, dict):
        nested = {}

    congestion = _pick_number(
        nested.get("congestion"),
        nested.get("congestionAvg"),
        nested.get("rltmCongestion"),
    )
    level_raw = _pick_number(
        nested.get("congestionLevel"),
        nested.get("level"),
        nested.get("congestionLvl"),
    )
    level = int(level_raw) if level_raw is not None else None
    if level is None and congestion is not None:
        if congestion < 0.025:
            level = 1
        elif congestion < 0.05:
            level = 2
        elif congestion < 0.3:
            level = 3
        else:
            level = 4

    return {
        "zoneId": poi.get("zoneId"),
        "zoneName": poi.get("zoneName"),
        "poiId": poi.get("poiId"),
        "poiName": poi.get("poiName"),
        "lat": poi.get("lat"),
        "lng": poi.get("lng"),
        "congestionPerSquareMeter": congestion,
        "congestionLevel": level,
        "congestionLabel": _level_label(level),
        "measuredAt": datetime.now(timezone.utc).isoformat(),
        "source": "sk_puzzle_place",
    }


class SkCongestionStore:
    """스레드 안전 SK 혼잡도 캐시."""

    def __init__(self):
        self._lock = threading.Lock()
        self.snapshot: dict = {
            "apiStatus": "idle",
            "message": "SK 혼잡도 대기 중",
            "places": [],
            "lastSuccessfulAt": None,
            "updatedAt": None,
        }
        self._quota_blocked_until = 0.0
        self._cache_at = 0.0

    def get(self) -> dict:
        with self._lock:
            return dict(self.snapshot)

    def set(self, data: dict):
        with self._lock:
            self.snapshot = {**data, "updatedAt": datetime.now(timezone.utc).isoformat()}


SK_STORE = SkCongestionStore()


def fetch_sk_congestion(force: bool = False) -> dict:
    """SK 장소 혼잡도 1회 조회 (캐시/쿼터 보호)."""
    now = time.time()
    cached = SK_STORE.get()
    if (
        not force
        and cached.get("apiStatus") in ("connected", "cached", "mock")
        and SK_STORE._cache_at
        and now - SK_STORE._cache_at < CACHE_TTL_SEC
    ):
        out = dict(cached)
        if out.get("apiStatus") == "connected":
            out["apiStatus"] = "cached"
            out["message"] = f"{cached.get('message', '')} (캐시)".strip()
        return out

    if not force and now < SK_STORE._quota_blocked_until:
        remain = int((SK_STORE._quota_blocked_until - now) / 60) + 1
        out = dict(cached)
        out["apiStatus"] = "quota_exceeded"
        out["message"] = f"SK API 한도로 {remain}분간 재호출 중단. 마지막 데이터 사용."
        return out

    app_key = os.environ.get("SK_OPEN_API_APP_KEY", "").strip()
    base = os.environ.get(
        "SK_PUZZLE_PLACE_BASE_URL",
        "https://apis.openapi.sk.com/puzzle/place/congestion/rltm/pois",
    )
    poi = _load_poi()
    if not poi:
        result = {
            "apiStatus": "failed",
            "message": "config/telecom.gwangalli.json POI 없음",
            "places": [],
            "lastSuccessfulAt": None,
        }
        SK_STORE.set(result)
        return result

    if not app_key:
        result = {
            "apiStatus": "missing_api_key",
            "message": "SK_OPEN_API_APP_KEY 없음 — 모의 표시",
            "places": [
                {
                    **{k: poi.get(k) for k in ("zoneId", "zoneName", "poiId", "poiName", "lat", "lng")},
                    "congestionPerSquareMeter": 0.04,
                    "congestionLevel": 2,
                    "congestionLabel": "보통",
                    "measuredAt": datetime.now(timezone.utc).isoformat(),
                    "source": "mock",
                }
            ],
            "lastSuccessfulAt": datetime.now(timezone.utc).isoformat(),
        }
        SK_STORE.set(result)
        SK_STORE._cache_at = now
        return result

    url = f"{base.rstrip('/')}/{poi['poiId']}?lat={poi['lat']}&lng={poi['lng']}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "appKey": app_key},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        status = exc.code
    except Exception as exc:
        result = {
            "apiStatus": "failed",
            "message": f"SK 호출 실패: {exc}",
            "places": cached.get("places") or [],
            "lastSuccessfulAt": cached.get("lastSuccessfulAt"),
        }
        SK_STORE.set(result)
        return result

    if status == 429 or "QUOTA" in text.upper():
        SK_STORE._quota_blocked_until = now + QUOTA_COOLDOWN_SEC
        result = {
            "apiStatus": "quota_exceeded",
            "message": "SK API 호출 한도 초과(429). 캐시/마지막 데이터 사용.",
            "places": cached.get("places") or [],
            "lastSuccessfulAt": cached.get("lastSuccessfulAt"),
        }
        SK_STORE.set(result)
        SK_STORE._cache_at = now
        return result

    if status == 403:
        result = {
            "apiStatus": "not_subscribed",
            "message": "SK 403 — 장소 혼잡도 상품 구독/권한 확인 필요",
            "places": cached.get("places") or [],
            "lastSuccessfulAt": cached.get("lastSuccessfulAt"),
        }
        SK_STORE.set(result)
        return result

    if status < 200 or status >= 300:
        result = {
            "apiStatus": "failed",
            "message": f"SK HTTP {status}: {text[:120]}",
            "places": cached.get("places") or [],
            "lastSuccessfulAt": cached.get("lastSuccessfulAt"),
        }
        SK_STORE.set(result)
        return result

    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        result = {
            "apiStatus": "failed",
            "message": "SK 응답 JSON 파싱 실패",
            "places": cached.get("places") or [],
            "lastSuccessfulAt": cached.get("lastSuccessfulAt"),
        }
        SK_STORE.set(result)
        return result

    place = _parse_payload(body, poi)
    result = {
        "apiStatus": "connected",
        "message": f"SK 장소 혼잡도 보조 ({poi.get('lat')}, {poi.get('lng')})",
        "places": [place],
        "lastSuccessfulAt": datetime.now(timezone.utc).isoformat(),
    }
    SK_STORE.set(result)
    SK_STORE._cache_at = now
    return result


def sk_refresh_loop(interval_sec: float = CACHE_TTL_SEC):
    """백그라운드에서 주기적으로 SK 혼잡도 갱신."""
    # 시작 직후 1회
    try:
        fetch_sk_congestion(force=True)
    except Exception as exc:
        SK_STORE.set(
            {
                "apiStatus": "failed",
                "message": f"초기 SK 조회 실패: {exc}",
                "places": [],
                "lastSuccessfulAt": None,
            }
        )
    while True:
        time.sleep(max(60.0, interval_sec))
        try:
            fetch_sk_congestion(force=False)
        except Exception:
            pass

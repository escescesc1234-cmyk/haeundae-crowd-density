# Safe Flow 상시 배포 (노트북 OFF)

노트북을 꺼도 API·AI 모델이 돌아가게 하려면 **항상 켜진 VPS/클라우드**에 Docker로 올립니다.

## 구성

| 서비스 | 포트 | 역할 |
|--------|------|------|
| `api` | 3780 | 밀도 API · 관광객/관리자 UI · 비전 상태 프록시 |
| `vision` | 8790 | YOLO 실시간 스트림 · `/api/status` · 가중치 로드 |

가중치 파일은 Git에 없습니다. 서버에  
`vision/models/yolo26s_beach_ft.pt` 를 직접 올립니다.

---

## 1) VPS 준비 (권장)

1. 클라우드에서 Ubuntu 22.04 VPS 생성  
   (예: AWS Lightsail, Oracle Cloud Free, Contabo, GCP e2-small 등)
2. SSH 접속 후 Docker 설치:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 재로그인 후
docker compose version
```

3. 방화벽에서 **3780, 8790** TCP 허용.

GPU가 없으면 실시간 탐지는 **느리지만 동작**합니다.  
빠른 AI가 필요하면 GPU 인스턴스 + NVIDIA Container Toolkit을 쓰세요.

---

## 2) 코드·모델 올리기

노트북(또는 CI)에서:

```bash
# 저장소
git clone https://github.com/escescesc1234-cmyk/haeundae-crowd-density.git
cd haeundae-crowd-density

# 가중치 전송 (로컬 → 서버)
scp vision/models/yolo26s_beach_ft.pt USER@YOUR_SERVER_IP:~/haeundae-crowd-density/vision/models/
```

서버에서:

```bash
cd ~/haeundae-crowd-density
mkdir -p vision/models
cp .env.deploy.example .env
# YOUR_SERVER_IP 를 실제 공인 IP(또는 도메인)로 수정
nano .env
```

`.env` 필수 예시:

```env
DENSITY_API_BASE_URL=http://203.0.113.10:3780
REALTIME_SAFETY_PUBLIC_URL=http://203.0.113.10:8790
```

---

## 3) 기동

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:3780/api/health
curl http://127.0.0.1:3780/api/vision/realtime/model
```

브라우저 확인:

- 관광객: `http://YOUR_SERVER_IP:3780/tourist.html`
- 관리자: `http://YOUR_SERVER_IP:3780/admin.html`
- 실시간 UI: `http://YOUR_SERVER_IP:8790/`

중지 / 로그:

```bash
docker compose logs -f --tail=100
docker compose down
```

---

## 4) 다른 작업자 Cursor 프롬프트

`localhost` 대신 **배포 URL**을 넣습니다.

```text
Safe Flow API baseUrl: http://YOUR_SERVER_IP:3780
DENSITY_API_BASE_URL=http://YOUR_SERVER_IP:3780
실시간 스트림: http://YOUR_SERVER_IP:8790/stream
(나머지 계약은 docs/INTEGRATION-PROMPT.md 와 동일)
```

노트북이 꺼져 있어도 이 URL만 살아 있으면 연동·AI 사용이 가능합니다.

---

## 5) 로컬에서 Docker로 시험 (선택)

Docker Desktop이 설치된 PC:

```bash
# 가중치 확인
dir vision\models\yolo26s_beach_ft.pt

copy .env.deploy.example .env
# DENSITY_API_BASE_URL=http://localhost:3780
# REALTIME_SAFETY_PUBLIC_URL=http://localhost:8790

docker compose up -d --build
```

이 PC에 Docker가 없으면 **VPS에만** 설치해도 됩니다.

---

## 제한·참고

- `POST /api/analyze/vision`(일회성 분석)은 API 컨테이너에 Python YOLO가 없어 **클라우드 기본 compose에서는 제외**에 가깝습니다.  
  상시 AI는 **`/api/vision/realtime/*` + `:8790/stream`** 을 쓰세요.
- 비밀키(`.env`)는 커밋하지 마세요.
- HTTPS/도메인이 필요하면 Nginx·Caddy를 앞에 두고 3780·8790을 리버스 프록시하면 됩니다.

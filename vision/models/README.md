# 모델 가중치 (Git 제외)

배포·실행 전 아래 파일을 이 폴더에 두세요.

- `yolo26s_beach_ft.pt` — Safe Flow 파인튜닝 가중치 (권장)

클라우드 배포 시 서버로 복사:

```bash
scp yolo26s_beach_ft.pt USER@SERVER:~/haeundae-crowd-density/vision/models/
```

자세한 절차: [`docs/DEPLOY.md`](../../docs/DEPLOY.md)

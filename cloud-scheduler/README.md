# Cloud Scheduler 트리거 (방식 A · 하이브리드)

GitHub Actions의 `schedule`(cron)은 **정시 실행이 보장되지 않아** 누락이 잦습니다
(특히 정각·고부하 시간대). 이 디렉터리는 그 트리거를 **Cloud Scheduler**로 옮겨
정시성을 확보하는 키트입니다. 워크플로의 파이썬 코드·러너·GitHub Secrets는 **그대로** 두고,
"언제 실행을 시작할지"만 Google 관리형 스케줄러가 담당합니다.

```
Cloud Scheduler(정시 보장, Asia/Seoul)
   └─HTTP POST──▶ GitHub API: workflow_dispatch
                     └─▶ 기존 GitHub Actions 워크플로 실행 ─▶ BigQuery 적재
```

## 왜 하이브리드인가
- 기존 자산(스크립트·시크릿·러너) 재사용 → 작업량 최소.
- Cloud Scheduler는 타임존을 직접 지정할 수 있어 **UTC 환산 불필요**(KST 그대로).
- 적재가 멱등(최근 N일 재적재/당일 전체 교체)이라, 혹시 GitHub cron과 겹쳐 이중 실행돼도 무해.

---

## 사전 준비 (1회)

### 0) gcloud 준비
```bash
gcloud auth login
gcloud config set project rf-ads-db-500505
```

### 1) 필요한 API 활성화
```bash
gcloud services enable cloudscheduler.googleapis.com secretmanager.googleapis.com \
  --project=rf-ads-db-500505
```

### 2) GitHub PAT 발급
GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
- Resource owner: `cloop-collab`, Repository access: **RF_D2C**만 선택
- Repository permissions: **Actions = Read and write** (그 외 불필요)
- 만료일 설정(예: 90일) — 만료 전 갱신 필요

### 3) PAT를 Secret Manager에 저장
```bash
printf '%s' "여기에_발급받은_PAT" | \
  gcloud secrets create github-pat-rf-d2c --data-file=- --project=rf-ads-db-500505
```
> 이미 있으면 새 버전 추가: `... | gcloud secrets versions add github-pat-rf-d2c --data-file=-`

---

## 배포
```bash
bash deploy.sh
```
있으면 update, 없으면 create 하므로 **여러 번 실행해도 안전**합니다.

### 등록되는 잡
| 잡 이름 | 스케줄(KST) | 워크플로 |
|---|---|---|
| rf-meta-daily | 매일 07:00 | meta_daily.yml |
| rf-naver-daily | 매일 06:00 | naver_daily.yml |
| rf-google-daily | 매일 07:00 | google_daily.yml |
| rf-ga4-daily | 매일 05:00 | ga4_daily.yml |
| rf-cafe24-daily | 매일 06:00 | cafe24_daily.yml |
| rf-meta-hourly | 매시 정각 | meta_hourly.yml |
| rf-naver-hourly | 매시 정각 | naver_hourly.yml |
| rf-ga4-intraday | 매시 정각 | ga4_d0_intraday.yml |
| rf-google-intraday | 매시 정각 | google_intraday.yml |
| rf-cafe24-intraday | 매시 정각 | cafe24_d0_intraday.yml |

> `meta_audit.yml`은 현재 GitHub에서 수동 비활성 상태라 기본 제외. 되살리려면 `deploy.sh`의 해당 줄 주석 해제.

---

## 확인 / 테스트
```bash
# 목록
gcloud scheduler jobs list --location=asia-northeast3 --project=rf-ads-db-500505

# 즉시 1건 테스트 실행 → GitHub Actions 탭에 해당 워크플로가 뜨면 성공
gcloud scheduler jobs run rf-meta-daily --location=asia-northeast3 --project=rf-ads-db-500505
```

---

## 마이그레이션 순서 (권장)
1. 이 키트로 Cloud Scheduler 잡 생성.
2. **약 1주일간 GitHub `schedule`과 병행** (이중 실행돼도 멱등이라 무해). Cloud Scheduler가 매일 정상 트리거되는지 관찰.
3. 안정 확인되면 각 워크플로의 `on.schedule:` 블록만 제거(`workflow_dispatch:`는 유지 — 수동 실행/스케줄러 트리거용). 이후 트리거는 Cloud Scheduler가 단독 담당.

## 운영
- **PAT 갱신**: 시크릿 새 버전 추가(`gcloud secrets versions add ...`) 후 `bash deploy.sh` 재실행.
- **스케줄 변경**: `deploy.sh`의 `JOBS` 배열에서 cron 수정 후 재실행.
- **실패 알림**: Cloud Scheduler 잡에 대해 Cloud Monitoring 알림(전송 실패율) 설정 권장. GitHub 쪽 실행 실패는 기존 Actions 알림 사용.

---

## 참고: 방식 B (완전 이전)
GitHub 의존을 완전히 없애려면 각 스크립트를 컨테이너(Cloud Run Job)로 배포하고
Cloud Scheduler가 OIDC로 직접 트리거하는 구조가 가장 견고합니다(PAT 불필요, 로그·재시도 GCP 일원화).
초기 구축 비용(Dockerfile + 배포 파이프라인)이 더 들어 별도 작업으로 진행 권장.

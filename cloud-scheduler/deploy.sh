#!/usr/bin/env bash
#
# 방식 A: Cloud Scheduler -> GitHub Actions workflow_dispatch (하이브리드)
# ---------------------------------------------------------------------------
# GitHub cron은 정시 실행이 보장되지 않아 누락이 잦다(특히 정각/고부하 시간대).
# 이 스크립트는 정시 실행이 보장되는 Cloud Scheduler가 각 워크플로를
# GitHub API로 트리거하게 만든다. 파이썬 코드·러너·시크릿은 그대로 유지된다.
#
# 사전 준비는 README.md 참고. 요약:
#   1) gcloud 로그인 + 프로젝트 지정
#   2) API 활성화: cloudscheduler.googleapis.com, secretmanager.googleapis.com
#   3) GitHub Fine-grained PAT 발급(RF_D2C 저장소, Actions: Read and write)
#   4) PAT를 Secret Manager에 저장 (기본 시크릿명: github-pat-rf-d2c)
#   5) 이 스크립트 실행:  bash deploy.sh
#
# 재실행해도 안전하다(있으면 update, 없으면 create). PAT 교체 시에도
# 시크릿만 새 버전으로 올리고 이 스크립트를 다시 돌리면 된다.
set -euo pipefail

# ===== 설정 (환경변수로 덮어쓸 수 있음) =====
PROJECT="${PROJECT:-rf-ads-db-500505}"           # Cloud Scheduler를 둘 GCP 프로젝트
REGION="${REGION:-asia-northeast3}"              # 서울 리전
REPO="${REPO:-cloop-collab/RF_D2C}"
TIMEZONE="${TIMEZONE:-Asia/Seoul}"               # KST로 직접 지정 (UTC 환산 불필요)
SECRET_NAME="${SECRET_NAME:-github-pat-rf-d2c}"  # Secret Manager에 저장한 GitHub PAT 이름

# PAT는 하드코딩하지 않고 Secret Manager에서 읽어온다.
echo "Secret Manager에서 PAT 로드: ${SECRET_NAME}"
PAT="$(gcloud secrets versions access latest --secret="${SECRET_NAME}" --project="${PROJECT}")"

# ===== 잡 정의: "잡이름|스케줄(KST)|워크플로 파일" =====
# 스케줄은 Asia/Seoul 기준. daily는 각 매체 원래 시각, hourly/intraday는 매시 정각.
JOBS=(
  "rf-meta-daily|0 7 * * *|meta_daily.yml"
  "rf-naver-daily|0 6 * * *|naver_daily.yml"
  "rf-google-daily|0 7 * * *|google_daily.yml"
  "rf-ga4-daily|0 5 * * *|ga4_daily.yml"
  "rf-cafe24-daily|0 6 * * *|cafe24_daily.yml"
  "rf-meta-hourly|0 * * * *|meta_hourly.yml"
  "rf-naver-hourly|0 * * * *|naver_hourly.yml"
  "rf-ga4-intraday|0 * * * *|ga4_d0_intraday.yml"
  "rf-google-intraday|0 * * * *|google_intraday.yml"
  "rf-cafe24-intraday|0 * * * *|cafe24_d0_intraday.yml"
  # "rf-meta-audit|0 * * * *|meta_audit.yml"   # 현재 GitHub에서 수동 비활성 상태. 되살리려면 주석 해제.
)

echo "프로젝트=${PROJECT} 리전=${REGION} 타임존=${TIMEZONE}"
echo "-----------------------------------------------------"

for entry in "${JOBS[@]}"; do
  IFS='|' read -r NAME SCHEDULE WF <<< "${entry}"
  URI="https://api.github.com/repos/${REPO}/actions/workflows/${WF}/dispatches"

  if gcloud scheduler jobs describe "${NAME}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
    ACTION="update"
  else
    ACTION="create"
  fi

  echo "==> [${ACTION}] ${NAME}  (${SCHEDULE} ${TIMEZONE})  ->  ${WF}"
  gcloud scheduler jobs "${ACTION}" http "${NAME}" \
    --location="${REGION}" \
    --project="${PROJECT}" \
    --schedule="${SCHEDULE}" \
    --time-zone="${TIMEZONE}" \
    --uri="${URI}" \
    --http-method=POST \
    --headers="Authorization=Bearer ${PAT},Accept=application/vnd.github+json,Content-Type=application/json" \
    --message-body='{"ref":"main"}' \
    --attempt-deadline=60s \
    --quiet
done

echo "-----------------------------------------------------"
echo "완료. 목록 확인:"
echo "  gcloud scheduler jobs list --location=${REGION} --project=${PROJECT}"
echo "테스트 실행(1건):"
echo "  gcloud scheduler jobs run rf-meta-daily --location=${REGION} --project=${PROJECT}"

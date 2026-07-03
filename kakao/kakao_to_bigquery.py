"""
Kakao Moment -> BigQuery 데이터 파이프라인
카카오모먼트 광고계정에서 일자별 성과를 가져와 BigQuery에 적재합니다.
creative_format 으로 디스플레이(DA) / 메시지(CRM)를 구분해 별도 테이블에 넣습니다.

  1) rf_kakao_moment  : 디스플레이(비즈보드) — creative_format 에 'image' 포함
  2) rf_kakao_message : 메시지(CRM)        — creative_format 에 'message' 포함 (비용만 추적)

실행 모드:
  backfill : 최근 N일치(BACKFILL_DAYS, 기본 365) 한 번에 적재 (최초 1회)
  daily    : 어제 하루치 (매일 새벽)

사용법:  python kakao_to_bigquery.py [backfill|daily]
설정은 모두 환경변수(GitHub Secrets)에서 읽습니다.

────────────────────────────────────────────────────────────────────
※스캐폴드 안내: 네이밍·테이블·날짜(KST)·BigQuery 적재 로직은 타 매체와 통일되어
  완성 상태입니다. 카카오모먼트 리포트 API의 정확한 "엔드포인트 / 파라미터 / 응답
  필드명"만 토큰 승인 후 공식 문서로 확정하면 됩니다. 확인이 필요한 지점은 코드에
  `※확인` 으로 표시해 두었습니다.
  공식 문서: https://developers.kakao.com/docs/latest/ko/kakaomoment/common
────────────────────────────────────────────────────────────────────
"""
import os
import sys
import datetime
import zoneinfo
import requests
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
DATASET = os.environ.get("BQ_DATASET", "kakao_moment")

# 카카오모먼트 API (※확인: 승인 후 공식 문서로 base/경로 확정)
KAKAO_API_BASE = os.environ.get("KAKAO_API_BASE", "https://apis.moment.kakao.com")
REPORT_PATH = os.environ.get("KAKAO_REPORT_PATH", "/openapi/v4/creatives/report")  # ※확인

TB_DA = "rf_kakao_moment"    # 디스플레이(DA)
TB_MSG = "rf_kakao_message"  # 메시지(CRM)

SF = bigquery.SchemaField

# ---------------- 테이블 스키마 ----------------

# 디스플레이(DA): 전체 성과
DA_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("campaign_id", "STRING"), SF("campaign_name", "STRING"),
    SF("ad_group_id", "STRING"), SF("ad_group_name", "STRING"),
    SF("creative_id", "STRING"), SF("creative_name", "STRING"),
    SF("creative_format", "STRING"),   # 카카오 원본 값
    SF("ad_type", "STRING"),           # 파생: DISPLAY / OTHER
    SF("impressions", "INTEGER"), SF("clicks", "INTEGER"), SF("cost", "FLOAT"),
    SF("conversions", "FLOAT"), SF("conversions_value", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# 메시지(CRM): 비용만 추적 (노출/클릭/전환은 카카오가 제공하지 않음)
MESSAGE_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("campaign_id", "STRING"), SF("campaign_name", "STRING"),
    SF("ad_group_id", "STRING"), SF("ad_group_name", "STRING"),
    SF("creative_id", "STRING"), SF("creative_name", "STRING"),
    SF("creative_format", "STRING"),
    SF("ad_type", "STRING"),           # 파생: MESSAGE
    SF("cost", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# ---------------- 공통 함수 ----------------

def date_range(mode):
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).date()  # 한국시간 기준
    if mode == "backfill":
        days = int(os.environ.get("BACKFILL_DAYS") or 365)
        return today - datetime.timedelta(days=days), today - datetime.timedelta(days=1)
    if mode == "daily":
        d = today - datetime.timedelta(days=1)
        return d, d
    raise ValueError(f"알 수 없는 모드: {mode}")


def _clean(v):
    return v.strip() if isinstance(v, str) else v


def _norm_date(v):
    """'20260702' 또는 '2026-07-02' → 'YYYY-MM-DD' (BigQuery DATE용)."""
    s = str(v or "").strip().replace(".", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def classify(creative_format):
    """creative_format 으로 광고유형 판별: message→MESSAGE, image→DISPLAY, 그 외→OTHER."""
    cf = (creative_format or "").lower()
    if "message" in cf:
        return "MESSAGE"
    if "image" in cf:
        return "DISPLAY"
    return "OTHER"


def fetch_report(access_token, account_id, start, end):
    """카카오모먼트 소재(creative) 일자별 리포트 → 표준 dict 리스트.

    ※확인: 엔드포인트/파라미터/응답 필드는 토큰 승인 후 공식 문서로 확정.
    아래는 표준 구조 기준 스캐폴드이며, `※확인` 부분만 실제 스펙에 맞추면 됩니다.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "adAccountId": str(account_id),   # ※확인: 헤더명(adAccountId) 확정
    }
    params = {
        "start": start.strftime("%Y%m%d"),   # ※확인: 날짜 파라미터/포맷
        "end": end.strftime("%Y%m%d"),
        "metricsGroup": "BASIC",             # ※확인: 지표 그룹
        "dimension": "CREATIVE",             # ※확인: 집계 단위
    }
    resp = requests.get(KAKAO_API_BASE + REPORT_PATH, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    rows = []
    # ※확인: 응답 컨테이너 키(data)와 dimensions/metrics 구조·필드명
    for item in payload.get("data", []):
        d = item.get("dimensions", item)
        m = item.get("metrics", item)
        rows.append({
            "date": _norm_date(d.get("start") or d.get("date")),   # ※확인
            "ad_account_id": str(account_id),
            "campaign_id": str(d.get("campaignId", "") or ""),
            "campaign_name": d.get("campaignName", "") or "",
            "ad_group_id": str(d.get("adGroupId", "") or ""),
            "ad_group_name": d.get("adGroupName", "") or "",
            "creative_id": str(d.get("creativeId", "") or ""),
            "creative_name": d.get("creativeName", "") or "",
            "creative_format": d.get("creativeFormat", "") or "",  # ※확인: 필드명
            "impressions": int(m.get("imp", 0) or 0),              # ※확인: 지표명
            "clicks": int(m.get("click", 0) or 0),
            "cost": float(m.get("cost", 0) or 0),
            "conversions": float(m.get("conv", 0) or 0),
            "conversions_value": float(m.get("convValue", 0) or 0),
        })
    return rows


def ensure_table(bq, table, schema):
    table_id = f"{PROJECT}.{DATASET}.{table}"
    try:
        bq.get_table(table_id)
    except NotFound:
        t = bigquery.Table(table_id, schema=schema)
        t.time_partitioning = bigquery.TimePartitioning(field="date")
        bq.create_table(t)
        print(f"[BigQuery] 테이블 생성: {table_id}")


def delete_range(bq, table, start, end):
    table_id = f"{PROJECT}.{DATASET}.{table}"
    bq.query(f"DELETE FROM `{table_id}` WHERE date BETWEEN '{start}' AND '{end}'").result()


def load_rows(bq, table, schema, rows):
    table_id = f"{PROJECT}.{DATASET}.{table}"
    if not rows:
        print(f"[{table}] 적재할 데이터 없음")
        return
    cfg = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")
    bq.load_table_from_json(rows, table_id, job_config=cfg).result()
    print(f"[{table}] {len(rows)}행 적재 완료")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RUN_MODE", "daily")
    start, end = date_range(mode)
    print(f"=== 모드={mode}  기간={start}~{end} ===")

    access_token = _clean(os.environ["KAKAO_ACCESS_TOKEN"])
    account_ids = [a.strip() for a in os.environ["KAKAO_AD_ACCOUNT_IDS"].split(",") if a.strip()]

    bq = bigquery.Client(project=PROJECT)
    ensure_table(bq, TB_DA, DA_SCHEMA)
    ensure_table(bq, TB_MSG, MESSAGE_SCHEMA)

    now = datetime.datetime.utcnow().isoformat()
    da_rows, msg_rows = [], []

    for acc in account_ids:
        print(f"[계정 {acc}] 리포트 수집 {start}~{end}")
        for r in fetch_report(access_token, acc, start, end):
            ad_type = classify(r["creative_format"])
            base = {
                "date": r["date"], "ad_account_id": r["ad_account_id"],
                "campaign_id": r["campaign_id"], "campaign_name": r["campaign_name"],
                "ad_group_id": r["ad_group_id"], "ad_group_name": r["ad_group_name"],
                "creative_id": r["creative_id"], "creative_name": r["creative_name"],
                "creative_format": r["creative_format"], "ad_type": ad_type,
                "loaded_at": now,
            }
            if ad_type == "MESSAGE":
                # 메시지: 비용만 별도 테이블로
                msg_rows.append({**base, "cost": r["cost"]})
            else:
                # 디스플레이(DA) 및 기타: 전체 성과
                da_rows.append({
                    **base,
                    "impressions": r["impressions"], "clicks": r["clicks"], "cost": r["cost"],
                    "conversions": r["conversions"], "conversions_value": r["conversions_value"],
                })

    # 확정 테이블: 해당 기간 삭제 후 적재 (재실행 안전 = 멱등)
    delete_range(bq, TB_DA, start, end)
    delete_range(bq, TB_MSG, start, end)
    load_rows(bq, TB_DA, DA_SCHEMA, da_rows)
    load_rows(bq, TB_MSG, MESSAGE_SCHEMA, msg_rows)
    print("=== 완료 ===")


if __name__ == "__main__":
    main()

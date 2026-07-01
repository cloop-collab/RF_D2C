#!/usr/bin/env python3
"""
GA4 -> BigQuery 적재 (cloop-collab/RF_D2C · ga4 폴더)
네이버(naver/naver_to_bigquery.py)와 동일한 규칙으로 통일.

프로젝트 rf-ads-db-500505 · 데이터셋 rf_ga4 (location: asia-northeast3)
  - rf_ga4      : 일별·확정. 최근 1년 유지, 매일 '전일자' 갱신 (cloop + sprint 합침)
  - rf_ga4_d0   : 당일. 시간당 갱신 (cloop + sprint 합침)
두 몰은 brand 컬럼(cloop / sprint)으로 구분.

실행:
  python ga4/ga4_to_bigquery.py --mode backfill   # 최초 1회: 지난 365일 rf_ga4 채우기
  python ga4/ga4_to_bigquery.py --mode daily      # 매일: 어제 데이터로 rf_ga4 갱신 + 1년 초과분 정리
  python ga4/ga4_to_bigquery.py --mode d0         # 시간당: 오늘 데이터로 rf_ga4_d0 갱신

인증: 환경변수 GOOGLE_APPLICATION_CREDENTIALS = 서비스계정 JSON 키 경로
"""

import argparse
import datetime as dt
from zoneinfo import ZoneInfo

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.cloud import bigquery

# ========== 설정 (여기만 고치면 됩니다) ==========
GCP_PROJECT = "rf-ads-db-500505"
BQ_DATASET = "rf_ga4"
BQ_LOCATION = "asia-northeast3"   # 서울
TABLE_DAILY = "rf_ga4"       # 일별·확정
TABLE_D0 = "rf_ga4_d0"       # 시간당·당일
BACKFILL_DAYS = 365
KST = ZoneInfo("Asia/Seoul")

# GA4 속성ID -> brand(몰) 이름
PROPERTIES = {
    "316130085": "cloop",    # 클룹몰
    "499489594": "sprint",   # 스프린트몰
}

DIMENSIONS = [
    "date",
    "sessionSource",
    "sessionMedium",
    "sessionDefaultChannelGroup",
    "sessionManualAdContent",
]
METRICS = [
    "sessions",
    "totalUsers",
    "newUsers",
    "addToCarts",
    "checkouts",
    "ecommercePurchases",
    "purchaseRevenue",
    "firstTimePurchasers",
]

DIM_COLS = {
    "date": "date",
    "sessionSource": "session_source",
    "sessionMedium": "session_medium",
    "sessionDefaultChannelGroup": "session_default_channel_group",
    "sessionManualAdContent": "session_manual_ad_content",
}
METRIC_COLS = {
    "sessions": "sessions",
    "totalUsers": "total_users",
    "newUsers": "new_users",
    "addToCarts": "add_to_carts",
    "checkouts": "checkouts",
    "ecommercePurchases": "ecommerce_purchases",
    "purchaseRevenue": "purchase_revenue",
    "firstTimePurchasers": "first_time_purchasers",
}
FLOAT_METRICS = {"purchase_revenue"}
# ===================================================


def bq_schema():
    fields = [
        bigquery.SchemaField("brand", "STRING"),
        bigquery.SchemaField("property_id", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("session_source", "STRING"),
        bigquery.SchemaField("session_medium", "STRING"),
        bigquery.SchemaField("session_default_channel_group", "STRING"),
        bigquery.SchemaField("session_manual_ad_content", "STRING"),
    ]
    for _, col in METRIC_COLS.items():
        bq_type = "FLOAT" if col in FLOAT_METRICS else "INTEGER"
        fields.append(bigquery.SchemaField(col, bq_type))
    fields.append(bigquery.SchemaField("loaded_at", "TIMESTAMP"))
    return fields


def fetch_property(client, property_id, brand, start_date, end_date):
    rows = []
    offset = 0
    page = 100000
    loaded_at = dt.datetime.now(dt.timezone.utc).isoformat()
    while True:
        req = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=d) for d in DIMENSIONS],
            metrics=[Metric(name=m) for m in METRICS],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            limit=page,
            offset=offset,
        )
        resp = client.run_report(request=req)
        for r in resp.rows:
            rec = {"brand": brand, "property_id": property_id}
            for i, d in enumerate(DIMENSIONS):
                val = r.dimension_values[i].value
                col = DIM_COLS[d]
                if col == "date":
                    rec[col] = dt.datetime.strptime(val, "%Y%m%d").date().isoformat()
                else:
                    rec[col] = val
            for i, m in enumerate(METRICS):
                col = METRIC_COLS[m]
                raw = r.metric_values[i].value or "0"
                rec[col] = float(raw) if col in FLOAT_METRICS else int(float(raw))
            rec["loaded_at"] = loaded_at
            rows.append(rec)
        total = resp.row_count or 0
        offset += page
        if offset >= total:
            break
    print(f"  - {brand}({property_id}) {start_date}~{end_date}: {len(rows)} rows")
    return rows


def fetch_all(start_date, end_date):
    client = BetaAnalyticsDataClient()
    all_rows = []
    for pid, brand in PROPERTIES.items():
        all_rows.extend(fetch_property(client, pid, brand, start_date, end_date))
    return all_rows


def ensure_dataset(bq):
    ds = bigquery.Dataset(f"{GCP_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    bq.create_dataset(ds, exists_ok=True)


def ensure_table(bq, table_id):
    full = f"{GCP_PROJECT}.{BQ_DATASET}.{table_id}"
    try:
        bq.get_table(full)
    except Exception:
        table = bigquery.Table(full, schema=bq_schema())
        table.time_partitioning = bigquery.TimePartitioning(field="date")
        bq.create_table(table)
        print(f"  * 테이블 생성: {full}")
    return full


def load_replace(bq, table_id, rows):
    full = ensure_table(bq, table_id)
    job_config = bigquery.LoadJobConfig(schema=bq_schema(), write_disposition="WRITE_TRUNCATE")
    bq.load_table_from_json(rows, full, job_config=job_config).result()
    print(f"[{table_id}] 전체 교체: {len(rows)} rows")


def load_merge_day(bq, table_id, rows, day):
    full = ensure_table(bq, table_id)
    bq.query(f"DELETE FROM `{full}` WHERE date = '{day}'").result()
    if rows:
        job_config = bigquery.LoadJobConfig(schema=bq_schema(), write_disposition="WRITE_APPEND")
        bq.load_table_from_json(rows, full, job_config=job_config).result()
    cutoff = (dt.datetime.now(KST).date() - dt.timedelta(days=BACKFILL_DAYS)).isoformat()
    bq.query(f"DELETE FROM `{full}` WHERE date < '{cutoff}'").result()
    print(f"[{table_id}] {day} 갱신 ({len(rows)} rows), {cutoff} 이전 정리")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["backfill", "daily", "d0"])
    args = ap.parse_args()

    bq = bigquery.Client(project=GCP_PROJECT)
    ensure_dataset(bq)
    today = dt.datetime.now(KST).date()

    if args.mode == "backfill":
        start = (today - dt.timedelta(days=BACKFILL_DAYS)).isoformat()
        end = (today - dt.timedelta(days=1)).isoformat()
        print(f"[backfill] {start} ~ {end}")
        load_replace(bq, TABLE_DAILY, fetch_all(start, end))
    elif args.mode == "daily":
        yday = (today - dt.timedelta(days=1)).isoformat()
        print(f"[daily] {yday}")
        load_merge_day(bq, TABLE_DAILY, fetch_all(yday, yday), yday)
    elif args.mode == "d0":
        d = today.isoformat()
        print(f"[d0] {d}")
        load_replace(bq, TABLE_D0, fetch_all(d, d))


if __name__ == "__main__":
    main()

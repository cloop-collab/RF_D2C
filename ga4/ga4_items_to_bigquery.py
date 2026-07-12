#!/usr/bin/env python3
"""
GA4 상품(item) 스코프 -> BigQuery 적재 (cloop-collab/RF_D2C · ga4 폴더)
ga4_to_bigquery.py(세션 스코프)와 동일 방식 · item 스코프 리포트만 다름.

프로젝트 rf-ads-db-500505 · 데이터셋 rf_ga4 (asia-northeast3)
  - rf_ga4_items    : 일별·확정 (cloop + sprint 합침) — 상품별 조회/장바구니/구매/매출
  - rf_ga4_items_d0 : 당일 (cloop + sprint 합침)

용도: 온사이트 상품 성과 차트 · 개당 평균단가(AOV) 추이 (윈저 GA4 item 리포트 대체).

모드:
  python ga4/ga4_items_to_bigquery.py --mode daily
     · 평소: 최근 LOOKBACK_DAYS(기본 7)일 재적재 + KEEP_DAYS 초과분 정리
     · BACKFILL_DAYS>0 로 실행 시: 과거 N일 1회 백필 (rf_ga4_items 전체 교체)
  python ga4/ga4_items_to_bigquery.py --mode d0
     · 당일 데이터로 rf_ga4_items_d0 교체 (시간당)
"""

import argparse
import os
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

# ========== 설정 (환경변수로 덮어쓰기 가능) ==========
GCP_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "rf_ga4")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
TABLE_DAILY = os.environ.get("BQ_TABLE", "rf_ga4_items")        # 일별·확정
TABLE_D0 = os.environ.get("BQ_TABLE_D0", "rf_ga4_items_d0")     # 시간당·당일
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or "7")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or "0")
KEEP_DAYS = int(os.environ.get("KEEP_DAYS") or "455")
KST = ZoneInfo("Asia/Seoul")

# GA4 속성ID -> brand(몰) 이름
PROPERTIES = {
    "316130085": "cloop",    # 클룹몰
    "499489594": "sprint",   # 스프린트몰
}

# GA4 차원 (item 스코프). date + item 식별자 (API 최대 9개 이내)
DIMENSIONS = [
    "date",
    "itemName",
    "itemId",
]
# GA4 지표 (item 스코프)
METRICS = [
    "itemsViewed",
    "itemsAddedToCart",
    "itemsCheckedOut",
    "itemsPurchased",
    "itemRevenue",
]

DIM_COLS = {
    "date": "date",
    "itemName": "item_name",
    "itemId": "item_id",
}
METRIC_COLS = {
    "itemsViewed": "items_viewed",
    "itemsAddedToCart": "items_added_to_cart",
    "itemsCheckedOut": "items_checked_out",
    "itemsPurchased": "items_purchased",
    "itemRevenue": "item_revenue",
}
FLOAT_METRICS = {"item_revenue"}
# =====================================================


def bq_schema():
    fields = [
        bigquery.SchemaField("brand", "STRING"),
        bigquery.SchemaField("property_id", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("item_name", "STRING"),
        bigquery.SchemaField("item_id", "STRING"),
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
    """테이블 전체 교체 (WRITE_TRUNCATE). 백필/당일 갱신용."""
    full = ensure_table(bq, table_id)
    job_config = bigquery.LoadJobConfig(
        schema=bq_schema(),
        write_disposition="WRITE_TRUNCATE",
    )
    bq.load_table_from_json(rows, full, job_config=job_config).result()
    print(f"[{table_id}] 전체 교체: {len(rows)} rows")


def load_merge_range(bq, table_id, rows, start, end):
    """[start, end] 구간만 지우고 다시 넣기 + KEEP_DAYS 초과분 정리."""
    full = ensure_table(bq, table_id)
    bq.query(f"DELETE FROM `{full}` WHERE date BETWEEN '{start}' AND '{end}'").result()
    if rows:
        job_config = bigquery.LoadJobConfig(
            schema=bq_schema(),
            write_disposition="WRITE_APPEND",
            schema_update_options=["ALLOW_FIELD_ADDITION"],
        )
        bq.load_table_from_json(rows, full, job_config=job_config).result()
    cutoff = (dt.datetime.now(KST).date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    bq.query(f"DELETE FROM `{full}` WHERE date < '{cutoff}'").result()
    print(f"[{table_id}] {start}~{end} 갱신 ({len(rows)} rows), {cutoff} 이전 정리")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["daily", "d0"])
    args = ap.parse_args()

    bq = bigquery.Client(project=GCP_PROJECT)
    ensure_dataset(bq)
    today = dt.datetime.now(KST).date()
    yesterday = today - dt.timedelta(days=1)

    if args.mode == "daily":
        if BACKFILL_DAYS > 0:
            start = (today - dt.timedelta(days=BACKFILL_DAYS)).isoformat()
            end = yesterday.isoformat()
            print(f"[items daily/backfill {BACKFILL_DAYS}d] {start} ~ {end}")
            load_replace(bq, TABLE_DAILY, fetch_all(start, end))
        else:
            start = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
            end = yesterday.isoformat()
            print(f"[items daily/lookback {LOOKBACK_DAYS}d] {start} ~ {end}")
            load_merge_range(bq, TABLE_DAILY, fetch_all(start, end), start, end)
    elif args.mode == "d0":
        d = today.isoformat()
        print(f"[items d0] {d}")
        load_replace(bq, TABLE_D0, fetch_all(d, d))


if __name__ == "__main__":
    main()

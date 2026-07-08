#!/usr/bin/env python3
"""
GA4 거래(transaction) 단위 -> BigQuery 적재 (cloop-collab/RF_D2C · ga4 폴더)

기존 ga4_to_bigquery.py 는 세션/캠페인 '집계'만 담는다(transaction_id 없음).
이 스크립트는 **거래(주문) 단위**로 뽑아, 카페24 order_id 매칭·거래별 매출 검증을 가능케 한다.
  - GA4 transactionId(=대개 카페24 order_id) 단위 purchase_revenue/tax/shipping
  - 세션 소스/매체/캠페인(=utm_campaign=광고세트명)으로 세트 분리
용도: R픽셀 분석 P1(GA4 txn ↔ 카페24 order 매칭)·P2(GA4 value vs 카페24 결제금액 과대 검증).

프로젝트 rf-ads-db-500505 · 데이터셋 rf_ga4 (asia-northeast3)
  - rf_ga4_transactions : 일별·확정 (cloop + sprint 합침, transaction 단위)

모드:
  python ga4/ga4_transactions_to_bigquery.py --mode daily
     · 평소: 최근 LOOKBACK_DAYS(기본 7)일 구간 재적재(지연 반영 보정) + KEEP_DAYS 초과분 정리
     · BACKFILL_DAYS>0 로 실행 시: 과거 N일 1회 백필(테이블 전체 교체)
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
TABLE_TX = os.environ.get("BQ_TABLE_TX", "rf_ga4_transactions")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or "7")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or "0")
KEEP_DAYS = int(os.environ.get("KEEP_DAYS") or "730")
KST = ZoneInfo("Asia/Seoul")

# GA4 속성ID -> brand(몰) 이름
PROPERTIES = {
    "316130085": "cloop",    # 클룹몰
    "499489594": "sprint",   # 스프린트몰
}

# 거래 단위 차원: transactionId + 세션 소스/캠페인. (date 포함 7개 <= API 9개 한도)
DIMENSIONS = [
    "date",
    "transactionId",
    "sessionSource",              # utm_source
    "sessionMedium",              # utm_medium
    "sessionCampaignName",        # utm_campaign = 광고세트명
    "sessionCampaignId",          # utm_id
    "sessionDefaultChannelGroup",
]
METRICS = [
    "purchaseRevenue",            # GA4 구매 value (카페24 결제금액과 비교 대상)
    "taxAmount",
    "shippingAmount",
    "ecommercePurchases",         # 거래당 구매 이벤트 수(중복 발화 시 >1)
]

DIM_COLS = {
    "date": "date",
    "transactionId": "transaction_id",
    "sessionSource": "session_source",
    "sessionMedium": "session_medium",
    "sessionCampaignName": "session_campaign_name",
    "sessionCampaignId": "session_campaign_id",
    "sessionDefaultChannelGroup": "session_default_channel_group",
}
METRIC_COLS = {
    "purchaseRevenue": "purchase_revenue",
    "taxAmount": "tax_amount",
    "shippingAmount": "shipping_amount",
    "ecommercePurchases": "ecommerce_purchases",
}
FLOAT_METRICS = {"purchase_revenue", "tax_amount", "shipping_amount"}
# transactionId 가 이 값이면 실제 거래가 아니므로 제외
NOT_TX = {"", "(not set)", "(not_set)"}
# =====================================================


def bq_schema():
    return [
        bigquery.SchemaField("brand", "STRING"),
        bigquery.SchemaField("property_id", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("transaction_id", "STRING"),          # = 카페24 order_id(대개)
        bigquery.SchemaField("session_source", "STRING"),          # utm_source
        bigquery.SchemaField("session_medium", "STRING"),          # utm_medium
        bigquery.SchemaField("session_campaign_name", "STRING"),   # utm_campaign = 세트명
        bigquery.SchemaField("session_campaign_id", "STRING"),     # utm_id
        bigquery.SchemaField("session_default_channel_group", "STRING"),
        bigquery.SchemaField("purchase_revenue", "FLOAT"),
        bigquery.SchemaField("tax_amount", "FLOAT"),
        bigquery.SchemaField("shipping_amount", "FLOAT"),
        bigquery.SchemaField("ecommerce_purchases", "INTEGER"),
        bigquery.SchemaField("loaded_at", "TIMESTAMP"),
    ]


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
            # 실제 거래가 아닌 행 제외
            if (rec.get("transaction_id") or "").strip() in NOT_TX:
                continue
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
    print(f"  - {brand}({property_id}) {start_date}~{end_date}: {len(rows)} tx rows")
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


def ensure_table(bq):
    full = f"{GCP_PROJECT}.{BQ_DATASET}.{TABLE_TX}"
    try:
        bq.get_table(full)
    except Exception:
        table = bigquery.Table(full, schema=bq_schema())
        table.time_partitioning = bigquery.TimePartitioning(field="date")
        table.clustering_fields = ["brand", "session_campaign_name"]
        bq.create_table(table)
        print(f"  * 테이블 생성: {full}")
    return full


def load_replace(bq, rows):
    """테이블 전체 교체 (WRITE_TRUNCATE). 백필용."""
    full = ensure_table(bq)
    job_config = bigquery.LoadJobConfig(
        schema=bq_schema(), write_disposition="WRITE_TRUNCATE",
    )
    bq.load_table_from_json(rows, full, job_config=job_config).result()
    print(f"[{TABLE_TX}] 전체 교체: {len(rows)} rows")


def load_merge_range(bq, rows, start, end):
    """[start, end] 구간만 지우고 다시 넣기 + KEEP_DAYS 초과분 정리."""
    full = ensure_table(bq)
    bq.query(f"DELETE FROM `{full}` WHERE date BETWEEN '{start}' AND '{end}'").result()
    if rows:
        job_config = bigquery.LoadJobConfig(
            schema=bq_schema(), write_disposition="WRITE_APPEND",
            schema_update_options=["ALLOW_FIELD_ADDITION"],
        )
        bq.load_table_from_json(rows, full, job_config=job_config).result()
    cutoff = (dt.datetime.now(KST).date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    bq.query(f"DELETE FROM `{full}` WHERE date < '{cutoff}'").result()
    print(f"[{TABLE_TX}] {start}~{end} 갱신 ({len(rows)} rows), {cutoff} 이전 정리")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="daily", choices=["daily", "d0"])
    args = ap.parse_args()

    bq = bigquery.Client(project=GCP_PROJECT)
    ensure_dataset(bq)
    today = dt.datetime.now(KST).date()
    yesterday = today - dt.timedelta(days=1)

    if args.mode == "d0":
        # 시간당 intraday: 오늘 파티션만 최신으로 덮어씀(같은 테이블). GA4 당일값은 지연·변동.
        d = today.isoformat()
        print(f"[d0/intraday] {d}")
        load_merge_range(bq, fetch_all(d, d), d, d)
        return

    if BACKFILL_DAYS > 0:
        start = (today - dt.timedelta(days=BACKFILL_DAYS)).isoformat()
        end = yesterday.isoformat()
        print(f"[daily/backfill {BACKFILL_DAYS}d] {start} ~ {end}")
        load_replace(bq, fetch_all(start, end))
    else:
        start = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
        end = yesterday.isoformat()
        print(f"[daily/lookback {LOOKBACK_DAYS}d] {start} ~ {end}")
        load_merge_range(bq, fetch_all(start, end), start, end)


if __name__ == "__main__":
    main()

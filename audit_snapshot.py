#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_snapshot.py
-----------------
매시간 실행되어, 계정별 최근 N일(기본 8일) 성과의 '현재 시점 스냅샷'을 append-only
감사 테이블(rf_meta_ads_audit)에 기록한다. 시간이 지나며 같은 report_date 의 숫자가
어떻게 보정되는지 추적 → "언제 100% 확정되는지" 확인용.
계정 단위(level=account)라 매우 가벼움.
"""
import os, json, logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
import requests
from google.cloud import bigquery

TOKEN = os.environ["META_ACCESS_TOKEN"]
ACCTS = os.environ.get("AD_ACCOUNT_IDS", "").split(",")
API = os.environ.get("META_API_VERSION", "v25.0")
PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
DATASET = os.environ.get("BQ_DATASET", "meta_ads")
TABLE = os.environ.get("BQ_AUDIT_TABLE", "rf_meta_ads_audit")
LOC = os.environ.get("BQ_LOCATION", "asia-northeast3")
DAYS = int(os.environ.get("AUDIT_DAYS", "8"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("audit")

PURCHASE = {"purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"}


def _sum_purchase(arr):
    if not arr:
        return 0.0
    return sum(float(a.get("value", 0) or 0) for a in arr if a.get("action_type") in PURCHASE)


def fetch(acct, since, until):
    url = f"https://graph.facebook.com/{API}/act_{acct}/insights"
    params = {
        "access_token": TOKEN, "level": "account", "time_increment": 1,
        "fields": "spend,impressions,clicks,actions,action_values",
        "time_range": json.dumps({"since": since, "until": until}), "limit": 200,
    }
    rows = []
    while url:
        r = requests.get(url, params=params, timeout=120)
        p = r.json()
        if "error" in p:
            raise RuntimeError(p["error"])
        rows += p.get("data", [])
        url = p.get("paging", {}).get("next")
        params = None
    return rows


def main():
    now = datetime.now(timezone.utc).isoformat()
    until = datetime.now(KST).date()
    since = until - timedelta(days=DAYS - 1)
    out = []
    for a in ACCTS:
        a = a.strip()
        if not a:
            continue
        for r in fetch(a, since.isoformat(), until.isoformat()):
            out.append({
                "snapshot_at": now,
                "report_date": r.get("date_start"),
                "account_id": r.get("account_id", a),
                "spend": float(r.get("spend", 0) or 0),
                "impressions": int(r.get("impressions", 0) or 0),
                "clicks": int(r.get("clicks", 0) or 0),
                "purchases": _sum_purchase(r.get("actions")),
                "purchase_value": _sum_purchase(r.get("action_values")),
            })

    schema = [
        bigquery.SchemaField("snapshot_at", "TIMESTAMP"),
        bigquery.SchemaField("report_date", "DATE"),
        bigquery.SchemaField("account_id", "STRING"),
        bigquery.SchemaField("spend", "FLOAT64"),
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("purchases", "FLOAT64"),
        bigquery.SchemaField("purchase_value", "FLOAT64"),
    ]
    client = bigquery.Client(project=PROJECT, location=LOC)
    client.create_dataset(bigquery.Dataset(f"{PROJECT}.{DATASET}"), exists_ok=True)
    tid = f"{PROJECT}.{DATASET}.{TABLE}"
    t = bigquery.Table(tid, schema=schema)
    t.time_partitioning = bigquery.TimePartitioning(field="report_date")
    client.create_table(t, exists_ok=True)
    if out:
        job = client.load_table_from_json(out, tid, job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition="WRITE_APPEND",
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON))
        job.result()
    log.info("스냅샷 %d행 기록 @ %s", len(out), now)


if __name__ == "__main__":
    main()

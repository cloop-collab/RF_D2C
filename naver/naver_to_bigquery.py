#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naver_to_bigquery.py
--------------------
네이버 검색광고(Search Ad, SA) 성과 데이터를 BigQuery에 적재하는 스크립트.
(RF_D2C 저장소의 meta_to_bigquery.py 와 동일한 구조/규칙)

동작 요약
  1) 두 계정(klup, sprint)의 캠페인 목록을 받아 id→이름 매핑을 만든다.
  2) 하루씩(day-by-day) /stats 를 호출해 캠페인 단위 성과를 가져온다.
  3) 캠페인명 기준으로 LP(자사몰/스마트스토어)를 태깅한다.
     - SA: 캠페인명에 '스토어' 또는 '스스' → 스마트스토어
     - DA: 캠페인명에 'asd' → 스마트스토어  (DA 연동 시 사용)
     - 그 외 → 자사몰
  4) 원본 응답 전체는 raw_json 컬럼에 보관한다.
  5) 데이터셋/테이블이 없으면 자동 생성(날짜 파티션 + 클러스터링).
  6) 적재는 날짜 파티션 단위 WRITE_TRUNCATE(덮어쓰기) → 중복 없음.
     - 일상(daily): 최근 LOOKBACK_DAYS 일 재적재
     - 시간당(hourly): LOOKBACK_DAYS=1 로 오늘 하루만 갱신
"""

import os
import re
import sys
import json
import time
import base64
import hashlib
import hmac
import logging
from datetime import date, datetime, timedelta, timezone

import requests
from google.cloud import bigquery

BASE_URL = "https://api.searchad.naver.com"

ACCOUNTS = [
    {
        "name": "CLOOP",
        "license": os.environ.get("NAVER_KLUP_LICENSE", ""),
        "secret": os.environ.get("NAVER_KLUP_SECRET", ""),
        "customer_id": os.environ.get("NAVER_KLUP_CUSTOMER_ID", "1762559"),
        "media": "SA",
    },
    {
        "name": "SPRINT",
        "license": os.environ.get("NAVER_SPRINT_LICENSE", ""),
        "secret": os.environ.get("NAVER_SPRINT_SECRET", ""),
        "customer_id": os.environ.get("NAVER_SPRINT_CUSTOMER_ID", "3750104"),
        "media": "SA",
    },
]

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
SLEEP_BETWEEN = float(os.environ.get("SLEEP_BETWEEN") or "0.3")
ID_CHUNK = int(os.environ.get("ID_CHUNK", "100"))

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "naver_ads")
BQ_TABLE = os.environ.get("BQ_TABLE", "rf_naver_sa_ads")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")

STAT_FIELDS = ["impCnt", "clkCnt", "salesAmt", "ctr", "cpc",
               "ccnt", "crto", "convAmt"]

NUMERIC = {"impCnt": "int", "clkCnt": "int", "salesAmt": "float",
           "ctr": "float", "cpc": "float", "ccnt": "float",
           "crto": "float", "convAmt": "float"}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("naver_to_bigquery")


def _signature(secret, timestamp, method, uri):
    message = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _headers(acct, method, uri):
    ts = str(int(time.time() * 1000))
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": acct["license"],
        "X-Customer": str(acct["customer_id"]),
        "X-Signature": _signature(acct["secret"], ts, method, uri),
    }


def _get(acct, uri, params=None, max_retries=5):
    last = ""
    for attempt in range(max_retries):
        resp = requests.get(BASE_URL + uri, params=params,
                            headers=_headers(acct, "GET", uri), timeout=60)
        if resp.status_code == 200:
            return resp.json()
        last = resp.text[:400]
        if resp.status_code in (429, 500, 502, 503):
            wait = min(60, 2 ** attempt * 2)
            log.warning("HTTP %s -> %d s retry (%d/%d)",
                        resp.status_code, wait, attempt + 1, max_retries)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {resp.status_code}: {last}")
    raise RuntimeError(f"retry exceeded: {last}")


def get_campaigns(acct):
    return _get(acct, "/ncc/campaigns")


def get_stats_for_day(acct, ids, day):
    params = {
        "ids": ids,
        "fields": json.dumps(STAT_FIELDS),
        "timeRange": json.dumps({"since": day, "until": day}),
    }
    res = _get(acct, "/stats", params=params)
    if isinstance(res, dict):
        return res.get("data", [])
    return res or []


def lp_type(media, campaign_name):
    name = campaign_name or ""
    if media == "SA" and re.search(r"스토어|스스", name):
        return "스마트스토어"
    if media == "DA" and re.search(r"asd", name):
        return "스마트스토어"
    return "자사몰"


def _num(rec, key):
    val = rec.get(key)
    if val is None or val == "":
        return None
    try:
        return int(float(val)) if NUMERIC.get(key) == "int" else float(val)
    except (TypeError, ValueError):
        return None


def transform(records, acct, id_to_name, day):
    out = []
    for rec in records:
        cid = rec.get("id") or rec.get("nccCampaignId")
        cname = id_to_name.get(cid, "")
        out.append({
            "report_date": day,
            "media": acct["media"],
            "account": acct["name"],
            "customer_id": str(acct["customer_id"]),
            "campaign_id": cid,
            "campaign_name": cname,
            "lp_type": lp_type(acct["media"], cname),
            "impressions": _num(rec, "impCnt"),
            "clicks": _num(rec, "clkCnt"),
            "cost": _num(rec, "salesAmt"),
            "ctr": _num(rec, "ctr"),
            "cpc": _num(rec, "cpc"),
            "conversions": _num(rec, "ccnt"),
            "conv_rate": _num(rec, "crto"),
            "conversion_value": _num(rec, "convAmt"),
            "raw_json": json.dumps(rec, ensure_ascii=False),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        })
    return out


def build_schema():
    return [
        bigquery.SchemaField("report_date", "DATE"),
        bigquery.SchemaField("media", "STRING"),
        bigquery.SchemaField("account", "STRING"),
        bigquery.SchemaField("customer_id", "STRING"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
        bigquery.SchemaField("lp_type", "STRING"),
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("cost", "FLOAT64"),
        bigquery.SchemaField("ctr", "FLOAT64"),
        bigquery.SchemaField("cpc", "FLOAT64"),
        bigquery.SchemaField("conversions", "FLOAT64"),
        bigquery.SchemaField("conv_rate", "FLOAT64"),
        bigquery.SchemaField("conversion_value", "FLOAT64"),
        bigquery.SchemaField("raw_json", "STRING"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]


def ensure_table(client):
    ds = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    client.create_dataset(ds, exists_ok=True)
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    table = bigquery.Table(table_id, schema=build_schema())
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY, field="report_date")
    table.clustering_fields = ["campaign_id", "lp_type"]
    client.create_table(table, exists_ok=True)
    log.info("table ready: %s", table_id)
    return table_id


def load_by_partition(client, table_id, rows):
    if not rows:
        log.info("no rows to load")
        return
    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        by_date[r.get("report_date")].append(r)
    schema = build_schema()
    for d, drows in sorted(by_date.items()):
        if not d:
            continue
        dest = f"{table_id}${d.replace('-', '')}"
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        client.load_table_from_json(drows, dest, job_config=job_config).result()
        log.info("loaded(overwrite) %s: %d rows", d, len(drows))


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def collect_rows(since_s, until_s):
    days = []
    cur = date.fromisoformat(since_s)
    end = date.fromisoformat(until_s)
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)

    rows = []
    for acct in ACCOUNTS:
        if not acct["license"] or not acct["secret"]:
            log.warning("[%s] empty key, skip", acct["name"])
            continue
        campaigns = get_campaigns(acct)
        id_to_name = {c["nccCampaignId"]: c.get("name", "") for c in campaigns}
        ids = list(id_to_name.keys())
        log.info("=== account %s: %d campaigns, %s~%s ===",
                 acct["name"], len(ids), since_s, until_s)
        if not ids:
            continue
        for day in days:
            for chunk in _chunks(ids, ID_CHUNK):
                data = get_stats_for_day(acct, chunk, day)
                rows.extend(transform(data, acct, id_to_name, day))
                time.sleep(SLEEP_BETWEEN)
    return rows


def main():
    if not any(a["license"] and a["secret"] for a in ACCOUNTS):
        log.error("네이버 키(LICENSE/SECRET) 환경변수가 비어 있습니다.")
        sys.exit(1)
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    table_id = ensure_table(client)

    until = date.today()
    since = until - timedelta(days=LOOKBACK_DAYS - 1)
    rows = collect_rows(since.isoformat(), until.isoformat())
    load_by_partition(client, table_id, rows)
    log.info("done: %d rows (%s ~ %s) -> %s",
             len(rows), since, until, table_id)


if __name__ == "__main__":
    main()

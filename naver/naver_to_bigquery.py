#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naver_to_bigquery.py
--------------------
네이버 검색광고(Search Ad, SA) 성과를 BigQuery에 적재.
NAVER_LEVEL 로 단위 선택: "campaign"(기본) 또는 "keyword".

  - campaign: 캠페인 단위 성과 → rf_naver_sa_ads
  - keyword : 캠페인>광고그룹>키워드 전체를 순회해 키워드 단위 성과 → rf_naver_sa_kw

공통 규칙
  * 하루씩 /stats 호출 → 날짜 파티션 단위 WRITE_TRUNCATE(덮어쓰기, 중복 없음)
  * 지표는 네이버가 주는 최대치 + raw_json 보관
  * LP 구분: SA=캠페인명 '스토어/스스'→스마트스토어, DA='asd'→스마트스토어, 그 외 자사몰
  * 모드: 일상(LOOKBACK_DAYS) / 백필(BACKFILL_DAYS>0, 예 365=1년)
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
from google.api_core.exceptions import NotFound

BASE_URL = "https://api.searchad.naver.com"

ACCOUNTS = [
    {
        "name": "CLOOP",
        "license": os.environ.get("NAVER_CLOOP_LICENSE", ""),
        "secret": os.environ.get("NAVER_CLOOP_SECRET", ""),
        "customer_id": os.environ.get("NAVER_CLOOP_CUSTOMER_ID", "1762559"),
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

LEVEL = os.environ.get("NAVER_LEVEL", "campaign").lower()   # campaign | keyword
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or "0")  # >0 이면 최근 N일 백필
SLEEP_BETWEEN = float(os.environ.get("SLEEP_BETWEEN") or "0.3")
ID_CHUNK = int(os.environ.get("ID_CHUNK", "100"))

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "naver_ads")
BQ_TABLE = os.environ.get("BQ_TABLE", "rf_naver_sa_ads" if LEVEL == "campaign" else "rf_naver_sa_kw")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")

# 네이버가 주는 최대 지표 (전환계열은 전환추적 미설정 시 거부될 수 있어 폴백 처리)
STAT_FIELDS = ["impCnt", "clkCnt", "salesAmt", "ctr", "cpc", "avgRnk",
               "ccnt", "crto", "cpConv", "convAmt", "ror"]
BASE_FIELDS = ["impCnt", "clkCnt", "salesAmt", "ctr", "cpc", "avgRnk"]

NUMERIC = {  # 값 캐스팅
    "impCnt": "int", "clkCnt": "int", "salesAmt": "float", "ctr": "float",
    "cpc": "float", "avgRnk": "float", "ccnt": "float", "crto": "float",
    "cpConv": "float", "convAmt": "float", "ror": "float",
}
# BigQuery 컬럼명 매핑
COLMAP = {
    "impCnt": "impressions", "clkCnt": "clicks", "salesAmt": "cost",
    "ctr": "ctr", "cpc": "cpc", "avgRnk": "avg_rank",
    "ccnt": "conversions", "crto": "conv_rate", "cpConv": "cost_per_conv",
    "convAmt": "conversion_value", "ror": "roas",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("naver_to_bigquery")


# ── API (HMAC 서명) ────────────────────────────────────────────────────────
def _signature(secret, ts, method, uri):
    msg = f"{ts}.{method}.{uri}"
    d = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(d).decode()


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
        r = requests.get(BASE_URL + uri, params=params,
                         headers=_headers(acct, "GET", uri), timeout=60)
        if r.status_code == 200:
            return r.json()
        last = r.text[:400]
        if r.status_code in (429, 500, 502, 503):
            wait = min(60, 2 ** attempt * 2)
            log.warning("HTTP %s -> %d s retry (%d/%d)", r.status_code, wait,
                        attempt + 1, max_retries)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {last}")
    raise RuntimeError(f"retry exceeded: {last}")


def get_campaigns(acct):
    return _get(acct, "/ncc/campaigns")


def get_adgroups(acct, campaign_id):
    return _get(acct, "/ncc/adgroups", params={"nccCampaignId": campaign_id})


def get_keywords(acct, adgroup_id):
    return _get(acct, "/ncc/keywords", params={"nccAdgroupId": adgroup_id})


def get_stats_for_day(acct, ids, day, fields):
    params = {
        "ids": ids,
        "fields": json.dumps(fields),
        "timeRange": json.dumps({"since": day, "until": day}),
    }
    res = _get(acct, "/stats", params=params)
    return res.get("data", []) if isinstance(res, dict) else (res or [])


def stats_with_fallback(acct, ids, day):
    """전체 지표로 시도, 실패하면 기본 지표로 폴백."""
    try:
        return get_stats_for_day(acct, ids, day, STAT_FIELDS)
    except RuntimeError as e:
        log.warning("전체 지표 실패(%s) → 기본 지표로 폴백", str(e)[:120])
        return get_stats_for_day(acct, ids, day, BASE_FIELDS)


# ── LP 구분 / 변환 ─────────────────────────────────────────────────────────
def lp_type(media, name):
    name = name or ""
    if media == "SA" and re.search(r"스토어|스스", name):
        return "스마트스토어"
    if media == "DA" and re.search(r"asd", name):
        return "스마트스토어"
    return "자사몰"


def _num(rec, key):
    v = rec.get(key)
    if v is None or v == "":
        return None
    try:
        return int(float(v)) if NUMERIC.get(key) == "int" else float(v)
    except (TypeError, ValueError):
        return None


def _metrics(rec):
    return {COLMAP[k]: _num(rec, k) for k in COLMAP}


# ── 스키마 ─────────────────────────────────────────────────────────────────
def _metric_schema():
    return [
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("cost", "FLOAT64"),
        bigquery.SchemaField("ctr", "FLOAT64"),
        bigquery.SchemaField("cpc", "FLOAT64"),
        bigquery.SchemaField("avg_rank", "FLOAT64"),
        bigquery.SchemaField("conversions", "FLOAT64"),
        bigquery.SchemaField("conv_rate", "FLOAT64"),
        bigquery.SchemaField("cost_per_conv", "FLOAT64"),
        bigquery.SchemaField("conversion_value", "FLOAT64"),
        bigquery.SchemaField("roas", "FLOAT64"),
    ]


def build_schema():
    head = [
        bigquery.SchemaField("report_date", "DATE"),
        bigquery.SchemaField("media", "STRING"),
        bigquery.SchemaField("account", "STRING"),
        bigquery.SchemaField("customer_id", "STRING"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
    ]
    if LEVEL == "keyword":
        head += [
            bigquery.SchemaField("adgroup_id", "STRING"),
            bigquery.SchemaField("adgroup_name", "STRING"),
            bigquery.SchemaField("keyword_id", "STRING"),
            bigquery.SchemaField("keyword", "STRING"),
        ]
    head += [bigquery.SchemaField("lp_type", "STRING")]
    tail = [
        bigquery.SchemaField("raw_json", "STRING"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]
    return head + _metric_schema() + tail


def ensure_table(client):
    ds = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    client.create_dataset(ds, exists_ok=True)
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    desired = build_schema()
    try:
        table = client.get_table(table_id)
        existing = {f.name for f in table.schema}
        missing = [f for f in desired if f.name not in existing]
        if missing:
            table.schema = list(table.schema) + missing
            client.update_table(table, ["schema"])
            log.info("스키마 컬럼 추가: %s", [f.name for f in missing])
        log.info("table ready(existing): %s (level=%s)", table_id, LEVEL)
    except NotFound:
        table = bigquery.Table(table_id, schema=desired)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field="report_date")
        table.clustering_fields = (["keyword_id", "campaign_id"] if LEVEL == "keyword"
                                   else ["campaign_id", "lp_type"])
        client.create_table(table)
        log.info("table created: %s (level=%s)", table_id, LEVEL)
    return table_id


# ── 적재 ───────────────────────────────────────────────────────────────────
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
        cfg = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        client.load_table_from_json(drows, dest, job_config=cfg).result()
        log.info("loaded(overwrite) %s: %d rows", d, len(drows))


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ── 수집: 캠페인 단위 ──────────────────────────────────────────────────────
def collect_campaign(acct, days):
    campaigns = get_campaigns(acct)
    id2name = {c["nccCampaignId"]: c.get("name", "") for c in campaigns}
    ids = list(id2name.keys())
    log.info("[%s] campaigns=%d", acct["name"], len(ids))
    rows = []
    for day in days:
        for chunk in _chunks(ids, ID_CHUNK):
            for rec in stats_with_fallback(acct, chunk, day):
                cid = rec.get("id")
                cname = id2name.get(cid, "")
                row = {
                    "report_date": day, "media": acct["media"],
                    "account": acct["name"], "customer_id": str(acct["customer_id"]),
                    "campaign_id": cid, "campaign_name": cname,
                    "lp_type": lp_type(acct["media"], cname),
                    "raw_json": json.dumps(rec, ensure_ascii=False),
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                }
                row.update(_metrics(rec))
                rows.append(row)
            time.sleep(SLEEP_BETWEEN)
    return rows


# ── 수집: 키워드 단위 ──────────────────────────────────────────────────────
def build_keyword_map(acct):
    """kwid -> {keyword, adgroup_id/name, campaign_id/name}"""
    kmap = {}
    campaigns = get_campaigns(acct)
    for c in campaigns:
        cid, cname = c["nccCampaignId"], c.get("name", "")
        try:
            adgroups = get_adgroups(acct, cid)
        except RuntimeError as e:
            log.warning("adgroups 실패 campaign=%s (%s)", cid, str(e)[:80])
            continue
        time.sleep(SLEEP_BETWEEN)
        for ag in adgroups:
            agid, agname = ag["nccAdgroupId"], ag.get("name", "")
            try:
                kws = get_keywords(acct, agid)
            except RuntimeError as e:
                log.warning("keywords 실패 adgroup=%s (%s)", agid, str(e)[:80])
                continue
            time.sleep(SLEEP_BETWEEN)
            for k in kws:
                kmap[k["nccKeywordId"]] = {
                    "keyword": k.get("keyword", ""),
                    "adgroup_id": agid, "adgroup_name": agname,
                    "campaign_id": cid, "campaign_name": cname,
                }
    return kmap


def collect_keyword(acct, days):
    kmap = build_keyword_map(acct)
    ids = list(kmap.keys())
    log.info("[%s] keywords=%d", acct["name"], len(ids))
    rows = []
    for day in days:
        for chunk in _chunks(ids, ID_CHUNK):
            for rec in stats_with_fallback(acct, chunk, day):
                kid = rec.get("id")
                meta = kmap.get(kid, {})
                cname = meta.get("campaign_name", "")
                row = {
                    "report_date": day, "media": acct["media"],
                    "account": acct["name"], "customer_id": str(acct["customer_id"]),
                    "campaign_id": meta.get("campaign_id"), "campaign_name": cname,
                    "adgroup_id": meta.get("adgroup_id"),
                    "adgroup_name": meta.get("adgroup_name"),
                    "keyword_id": kid, "keyword": meta.get("keyword", ""),
                    "lp_type": lp_type(acct["media"], cname),
                    "raw_json": json.dumps(rec, ensure_ascii=False),
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                }
                row.update(_metrics(rec))
                rows.append(row)
            time.sleep(SLEEP_BETWEEN)
    return rows


def collect_rows(since_s, until_s):
    days = []
    cur, end = date.fromisoformat(since_s), date.fromisoformat(until_s)
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    rows = []
    for acct in ACCOUNTS:
        if not acct["license"] or not acct["secret"]:
            log.warning("[%s] empty key, skip", acct["name"])
            continue
        log.info("=== account %s (level=%s, %s~%s) ===",
                 acct["name"], LEVEL, since_s, until_s)
        rows += collect_keyword(acct, days) if LEVEL == "keyword" else collect_campaign(acct, days)
    return rows


def main():
    if LEVEL not in ("campaign", "keyword"):
        log.error("NAVER_LEVEL 은 campaign 또는 keyword 여야 합니다.")
        sys.exit(1)
    if not any(a["license"] and a["secret"] for a in ACCOUNTS):
        log.error("네이버 키(LICENSE/SECRET) 환경변수가 비어 있습니다.")
        sys.exit(1)
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    table_id = ensure_table(client)

    until = date.today()
    span = BACKFILL_DAYS if BACKFILL_DAYS > 0 else LOOKBACK_DAYS
    if BACKFILL_DAYS > 0:
        log.info("백필 모드: 최근 %d일 채우기", BACKFILL_DAYS)
    since = until - timedelta(days=span - 1)
    rows = collect_rows(since.isoformat(), until.isoformat())
    load_by_partition(client, table_id, rows)
    log.info("done: %d rows (%s ~ %s) -> %s", len(rows), since, until, table_id)


if __name__ == "__main__":
    main()

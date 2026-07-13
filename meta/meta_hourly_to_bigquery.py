#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meta_hourly_to_bigquery.py
--------------------------
메타 광고 Insights 를 '시간대(0~23시)' 브레이크다운으로 BigQuery에 적재.
대시보드 시간대별 Meta 드릴다운(광고탭 일자 클릭 → 0~23시) 패널용.

  · breakdowns=hourly_stats_aggregated_by_advertiser_time_zone (광고주 시간대=KST 기준 시간대)
  · level=ad, time_increment=1  → (일자 × 시간 × 광고) 그레인
  · 지표: impressions·clicks·spend 만(슬림). CPM/CPC/CTR 은 대시보드에서 파생.
    (Meta hourly 브레이크다운은 omni/구매전환 신뢰도 낮아 전환은 제외.)
  · 출력: meta_ads.rf_meta_ads_hourly (report_date 파티션 · campaign_id 클러스터)
  · 적재: 날짜 파티션 WRITE_TRUNCATE(멱등)

일상: 최근 LOOKBACK_DAYS(기본3)일. 백필: META_SINCE/META_UNTIL(YYYY-MM-DD) 지정.
비고: 원가엔진 무관. 기존 meta_to_bigquery.py(일별 rf_meta_ads)의 비동기 리포트 방식 이식.
"""
import os
import sys
import json
import time
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

KST = ZoneInfo("Asia/Seoul")

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "여기에_토큰")
AD_ACCOUNT_IDS = os.environ.get("AD_ACCOUNT_IDS", "여기에_계정").split(",")
API_VERSION = os.environ.get("META_API_VERSION", "v25.0")
LEVEL = "ad"
BREAKDOWN = "hourly_stats_aggregated_by_advertiser_time_zone"
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or "3")
SLEEP_BETWEEN = int(os.environ.get("SLEEP_BETWEEN") or "15")

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "meta_ads")
BQ_TABLE = os.environ.get("BQ_TABLE", "rf_meta_ads_hourly")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")

# account_id → mall (rf_meta_ads 실측 매핑)
MALL_BY_ACCT = {"1462607070849777": "cloop", "3342733785912061": "sprint"}

FIELDS = [
    "account_id", "account_name",
    "campaign_id", "campaign_name",
    "adset_id", "adset_name",
    "ad_id", "ad_name",
    "impressions", "clicks", "spend",
    "date_start", "date_stop",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("meta_hourly")


# ── 메타 비동기 리포트 (meta_to_bigquery.py 이식) ──────────────────────────
def _is_rate_limit(err):
    if not err:
        return False
    code = err.get("code", err.get("error_code"))
    sub = err.get("error_subcode")
    msg = (err.get("error_message") or err.get("message") or "").lower()
    if code in (4, 17, 32, 613):
        return True
    if sub in (1504022, 2446079, 1487742):
        return True
    return "request limit" in msg or "limit reached" in msg or "too many" in msg


def _request_with_retry(url, params, max_retries=6):
    last = ""
    for attempt in range(max_retries):
        resp = requests.get(url, params=params, timeout=120)
        if resp.status_code == 200:
            return resp
        last = resp.text[:400]
        if resp.status_code in (429, 500, 502, 503):
            wait = min(60, 2 ** attempt * 2)
            log.warning("HTTP %s → %d초 후 재시도(%d/%d)", resp.status_code, wait, attempt + 1, max_retries)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {resp.status_code}: {last}")
    raise RuntimeError(f"재시도 한도 초과: {last}")


def _start_async_report(account_id, since, until):
    url = f"https://graph.facebook.com/{API_VERSION}/act_{account_id}/insights"
    data = {
        "access_token": META_ACCESS_TOKEN,
        "level": LEVEL,
        "time_increment": 1,
        "breakdowns": BREAKDOWN,
        "fields": ",".join(FIELDS),
        "time_range": json.dumps({"since": since, "until": until}),
    }
    resp = requests.post(url, data=data, timeout=120)
    payload = resp.json()
    if "error" in payload:
        return None, payload["error"]
    return payload.get("report_run_id"), None


def _wait_async_report(run_id, max_wait=1800, interval=10):
    url = f"https://graph.facebook.com/{API_VERSION}/{run_id}"
    waited = 0
    while waited <= max_wait:
        s = _request_with_retry(url, {"access_token": META_ACCESS_TOKEN}).json()
        status = s.get("async_status")
        if status == "Job Completed":
            return "Job Completed", s
        if status in ("Job Failed", "Job Skipped"):
            return status, s
        log.info("리포트 진행중... %s%% (%s)", s.get("async_percent_completion"), status)
        time.sleep(interval)
        waited += interval
    return "Timeout", {"async_status": "Timeout"}


def _fetch_async_results(run_id):
    url = f"https://graph.facebook.com/{API_VERSION}/{run_id}/insights"
    params = {"access_token": META_ACCESS_TOKEN, "limit": 500}
    rows = []
    while url:
        payload = _request_with_retry(url, params).json()
        if "error" in payload:
            raise RuntimeError(f"메타 API 오류(결과 조회): {payload['error']}")
        rows.extend(payload.get("data", []))
        url = payload.get("paging", {}).get("next")
        params = None
    return rows


def fetch_insights(account_id, since, until):
    backoff = 60
    for attempt in range(8):
        run_id, err = _start_async_report(account_id, since, until)
        if err:
            if _is_rate_limit(err):
                log.warning("요청 한도(생성) → %d초 대기", backoff); time.sleep(backoff); backoff = min(backoff * 2, 900); continue
            raise RuntimeError(f"메타 API 오류(리포트 생성): {err}")
        status, info = _wait_async_report(run_id)
        if status == "Job Completed":
            rows = _fetch_async_results(run_id)
            log.info("계정 %s: %d행 수집", account_id, len(rows))
            return rows
        if _is_rate_limit(info):
            log.warning("리포트 실패(요청 한도) → %d초 대기", backoff); time.sleep(backoff); backoff = min(backoff * 2, 900); continue
        raise RuntimeError(f"비동기 작업 실패: {info}")
    raise RuntimeError("요청 한도 지속 — 재시도 한도 초과")


# ── 변환 ────────────────────────────────────────────────────────────────
def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def transform(rows, account_id):
    out = []
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        hb = r.get(BREAKDOWN)  # "HH:00:00 - HH:59:59"
        hour = _to_int(hb[:2]) if hb else None
        acct = r.get("account_id") or account_id
        out.append({
            "report_date": r.get("date_start"),
            "hour": hour,
            "mall": MALL_BY_ACCT.get(str(acct)),
            "account_id": str(acct) if acct is not None else None,
            "account_name": r.get("account_name"),
            "campaign_id": r.get("campaign_id"),
            "campaign_name": r.get("campaign_name"),
            "adset_id": r.get("adset_id"),
            "adset_name": r.get("adset_name"),
            "ad_id": r.get("ad_id"),
            "ad_name": r.get("ad_name"),
            "impressions": _to_int(r.get("impressions")),
            "clicks": _to_int(r.get("clicks")),
            "spend": _to_float(r.get("spend")),
            "ingested_at": now,
        })
    return out


# ── BigQuery ────────────────────────────────────────────────────────────
def build_schema():
    return [
        bigquery.SchemaField("report_date", "DATE"),
        bigquery.SchemaField("hour", "INT64"),
        bigquery.SchemaField("mall", "STRING"),
        bigquery.SchemaField("account_id", "STRING"),
        bigquery.SchemaField("account_name", "STRING"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
        bigquery.SchemaField("adset_id", "STRING"),
        bigquery.SchemaField("adset_name", "STRING"),
        bigquery.SchemaField("ad_id", "STRING"),
        bigquery.SchemaField("ad_name", "STRING"),
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("spend", "FLOAT64"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]


def ensure_table(client):
    ds = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    client.create_dataset(ds, exists_ok=True)
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    try:
        client.get_table(table_id)
    except NotFound:
        t = bigquery.Table(table_id, schema=build_schema())
        t.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field="report_date")
        t.clustering_fields = ["campaign_id"]
        client.create_table(t)
        log.info("테이블 생성: %s", table_id)
    return table_id


def load_by_partition(client, table_id, rows):
    if not rows:
        log.info("적재할 행 없음")
        return
    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        if r.get("report_date"):
            by_date[r["report_date"]].append(r)
    schema = build_schema()
    for d, drows in sorted(by_date.items()):
        dest = f"{table_id}${d.replace('-', '')}"
        cfg = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        client.load_table_from_json(drows, dest, job_config=cfg).result()
        log.info("적재(덮어쓰기) %s: %d행", d, len(drows))


def _collect(since_s, until_s):
    rows = []
    for acct in AD_ACCOUNT_IDS:
        acct = acct.strip()
        if not acct:
            continue
        log.info("=== 계정 %s (%s ~ %s) ===", acct, since_s, until_s)
        rows.extend(transform(fetch_insights(acct, since_s, until_s), acct))
        time.sleep(SLEEP_BETWEEN)
    return rows


def main():
    if "여기에" in META_ACCESS_TOKEN or "여기에" in "".join(AD_ACCOUNT_IDS):
        log.error("META_ACCESS_TOKEN / AD_ACCOUNT_IDS 필요"); sys.exit(1)
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    table_id = ensure_table(client)
    since_s = os.environ.get("META_SINCE", "").strip()
    until_s = os.environ.get("META_UNTIL", "").strip()
    if since_s and until_s:
        log.info("백필 %s ~ %s", since_s, until_s)
        rows = _collect(since_s, until_s)
    else:
        until = datetime.now(KST).date()
        since = until - timedelta(days=LOOKBACK_DAYS - 1)
        log.info("일상 최근 %d일 %s ~ %s", LOOKBACK_DAYS, since, until)
        rows = _collect(since.isoformat(), until.isoformat())
    load_by_partition(client, table_id, rows)
    log.info("완료: 총 %d행 → %s", len(rows), table_id)


if __name__ == "__main__":
    main()

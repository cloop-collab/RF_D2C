#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meta_to_bigquery.py
-------------------
메타(Facebook/Instagram) 광고 Insights 데이터를 BigQuery에 적재하는 스크립트.

동작 요약
  1) 메타 Marketing API의 insights 를 '비동기 리포트'로 ad 단위, 일별(time_increment=1)
     로 가져온다. (큰 계정에서도 rate limit 없이 안전)
  2) 숫자형 지표는 컬럼으로, 중첩(actions 등)은 JSON 문자열로, 원본 응답 전체는
     raw_json 컬럼에 보관한다. → "조회 가능한 모든 데이터" 보존
  3) BigQuery 데이터셋/테이블이 없으면 자동 생성한다. (날짜 파티션 + 클러스터링)
  4) 적재는 날짜 파티션 단위 WRITE_TRUNCATE(덮어쓰기) → 중복 없음, 결제 없이도 동작.
  5) 두 가지 모드:
     - 일상(daily): 최근 LOOKBACK_DAYS 일을 다시 받아 덮어쓰기 (BACKFILL_MONTHS=0)
     - 백필(backfill): 과거 N개월을 달 단위로 1회 채우기 (BACKFILL_MONTHS=37 등)
"""

import os
import sys
import json
import time
import logging
from datetime import date, datetime, timedelta, timezone

import requests
from google.cloud import bigquery

# ──────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "여기에_시스템유저_토큰")
AD_ACCOUNT_IDS = os.environ.get("AD_ACCOUNT_IDS", "여기에_광고계정ID").split(",")

API_VERSION = os.environ.get("META_API_VERSION", "v25.0")
LEVEL = os.environ.get("META_LEVEL", "ad")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
# 백필(1회성): 과거 N개월을 한 번에 채울 때만 설정 (예: 37). 0/빈값이면 일상(daily) 모드.
BACKFILL_MONTHS = int(os.environ.get("BACKFILL_MONTHS") or "0")

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "meta_ads")
BQ_TABLE = os.environ.get("BQ_TABLE", "rf_ads_db")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")

# 서비스계정 키 경로 (환경변수로 안 넣었으면 이 파일 옆의 키를 자동 사용)
_DEFAULT_KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "rf-ads-db-500505-ccd04a9d1879.json")
if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.path.exists(_DEFAULT_KEY):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _DEFAULT_KEY

# ──────────────────────────────────────────────────────────────────────────
# 필드 정의
# ──────────────────────────────────────────────────────────────────────────
SCALAR_FIELDS = [
    "account_id", "account_name", "account_currency",
    "campaign_id", "campaign_name",
    "adset_id", "adset_name",
    "ad_id", "ad_name",
    "objective", "optimization_goal", "buying_type",
    "spend", "impressions", "clicks", "reach", "frequency",
    "cpc", "cpm", "cpp", "ctr",
    "unique_clicks", "unique_ctr", "cost_per_unique_click",
    "inline_link_clicks", "inline_link_click_ctr", "cost_per_inline_link_click",
    "inline_post_engagement",
    "date_start", "date_stop",
]

NESTED_FIELDS = [
    "actions", "action_values",
    "conversions", "conversion_values",
    "cost_per_action_type", "cost_per_conversion",
    "purchase_roas", "website_purchase_roas",
    "video_play_actions",
    "video_p25_watched_actions", "video_p50_watched_actions",
    "video_p75_watched_actions", "video_p100_watched_actions",
    "video_avg_time_watched_actions",
    "outbound_clicks", "unique_outbound_clicks",
]

NUMERIC_FIELDS = {
    "spend", "impressions", "clicks", "reach", "frequency",
    "cpc", "cpm", "cpp", "ctr",
    "unique_clicks", "unique_ctr", "cost_per_unique_click",
    "inline_link_clicks", "inline_link_click_ctr", "cost_per_inline_link_click",
    "inline_post_engagement",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("meta_to_bigquery")


# ──────────────────────────────────────────────────────────────────────────
# 1) 메타에서 데이터 가져오기 (비동기 리포트)
# ──────────────────────────────────────────────────────────────────────────
def fetch_insights(account_id, since, until):
    all_fields = SCALAR_FIELDS + NESTED_FIELDS
    run_id = None
    for _ in range(len(all_fields) + 1):
        run_id, err = _start_async_report(account_id, all_fields, since, until)
        if run_id:
            break
        msg = (err or {}).get("message", "")
        if "nonexisting field" in msg or "(#100)" in msg:
            bad = _extract_bad_field(msg)
            if bad and bad in all_fields:
                log.warning("필드 '%s' 거부됨 → 제외하고 재시도", bad)
                all_fields.remove(bad)
                continue
        raise RuntimeError(f"메타 API 오류(리포트 생성): {err}")
    if not run_id:
        raise RuntimeError("비동기 리포트 생성 실패")

    _wait_async_report(run_id)
    rows = _fetch_async_results(run_id)
    log.info("계정 %s: %d행 수집", account_id, len(rows))
    return rows


def _start_async_report(account_id, fields, since, until):
    url = f"https://graph.facebook.com/{API_VERSION}/act_{account_id}/insights"
    data = {
        "access_token": META_ACCESS_TOKEN,
        "level": LEVEL,
        "time_increment": 1,
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since, "until": until}),
    }
    resp = requests.post(url, data=data, timeout=120)
    payload = resp.json()
    if "error" in payload:
        return None, payload["error"]
    return payload.get("report_run_id"), None


def _wait_async_report(run_id, max_wait=1800, interval=5):
    url = f"https://graph.facebook.com/{API_VERSION}/{run_id}"
    waited = 0
    while waited <= max_wait:
        resp = _request_with_retry(url, {"access_token": META_ACCESS_TOKEN})
        s = resp.json()
        status = s.get("async_status")
        pct = s.get("async_percent_completion")
        if status == "Job Completed":
            return
        if status in ("Job Failed", "Job Skipped"):
            raise RuntimeError(f"비동기 작업 실패({status}): {s}")
        log.info("리포트 진행중... %s%% (%s)", pct, status)
        time.sleep(interval)
        waited += interval
    raise RuntimeError("비동기 작업 시간 초과")


def _fetch_async_results(run_id):
    url = f"https://graph.facebook.com/{API_VERSION}/{run_id}/insights"
    params = {"access_token": META_ACCESS_TOKEN, "limit": 200}
    rows = []
    while url:
        resp = _request_with_retry(url, params)
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"메타 API 오류(결과 조회): {payload['error']}")
        rows.extend(payload.get("data", []))
        url = payload.get("paging", {}).get("next")
        params = None
    return rows


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


def _extract_bad_field(message):
    if "(" in message and ")" in message:
        try:
            return message.rsplit("(", 1)[1].split(")")[0].strip()
        except Exception:
            return None
    return None


# ──────────────────────────────────────────────────────────────────────────
# 2) 응답 → BigQuery 행 변환
# ──────────────────────────────────────────────────────────────────────────
def transform(rows, account_id):
    out = []
    for r in rows:
        row = {"raw_json": json.dumps(r, ensure_ascii=False)}
        for f in SCALAR_FIELDS:
            val = r.get(f)
            if val is None:
                row[f] = None
            elif f in NUMERIC_FIELDS:
                try:
                    row[f] = float(val)
                except (TypeError, ValueError):
                    row[f] = None
            else:
                row[f] = str(val)
        for f in NESTED_FIELDS:
            row[f] = json.dumps(r[f], ensure_ascii=False) if f in r else None
        row["report_date"] = r.get("date_start")
        if not row.get("account_id"):
            row["account_id"] = account_id
        row["ingested_at"] = datetime.now(timezone.utc).isoformat()
        out.append(row)
    return out


# ──────────────────────────────────────────────────────────────────────────
# 3) BigQuery 스키마/테이블
# ──────────────────────────────────────────────────────────────────────────
def build_schema():
    schema = []
    for f in SCALAR_FIELDS:
        schema.append(bigquery.SchemaField(f, "FLOAT64" if f in NUMERIC_FIELDS else "STRING"))
    for f in NESTED_FIELDS:
        schema.append(bigquery.SchemaField(f, "STRING"))
    schema.append(bigquery.SchemaField("report_date", "DATE"))
    schema.append(bigquery.SchemaField("raw_json", "STRING"))
    schema.append(bigquery.SchemaField("ingested_at", "TIMESTAMP"))
    return schema


def ensure_table(client):
    ds_ref = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_DATASET}")
    ds_ref.location = BQ_LOCATION
    client.create_dataset(ds_ref, exists_ok=True)
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    table = bigquery.Table(table_id, schema=build_schema())
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY, field="report_date")
    table.clustering_fields = ["account_id", "campaign_id"]
    client.create_table(table, exists_ok=True)
    log.info("테이블 준비 완료: %s", table_id)
    return table_id


# ──────────────────────────────────────────────────────────────────────────
# 4) 멱등 적재: 날짜 파티션 단위 덮어쓰기 (로드 잡, 결제 불필요)
# ──────────────────────────────────────────────────────────────────────────
def load_by_partition(client, table_id, rows):
    if not rows:
        log.info("적재할 행 없음")
        return
    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        by_date[r.get("report_date")].append(r)
    schema = build_schema()
    for d, drows in sorted(by_date.items()):
        if not d:
            continue
        partition = d.replace("-", "")
        dest = f"{table_id}${partition}"
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        job = client.load_table_from_json(drows, dest, job_config=job_config)
        job.result()
        log.info("적재(덮어쓰기) %s: %d행", d, len(drows))


# ──────────────────────────────────────────────────────────────────────────
# 5) 실행 모드
# ──────────────────────────────────────────────────────────────────────────
def _collect_rows(since_s, until_s):
    rows = []
    for acct in AD_ACCOUNT_IDS:
        acct = acct.strip()
        if not acct:
            continue
        log.info("=== 계정 %s (%s ~ %s) ===", acct, since_s, until_s)
        raw = fetch_insights(acct, since_s, until_s)
        rows.extend(transform(raw, acct))
    return rows


def run_daily(client, table_id):
    until = date.today()
    since = until - timedelta(days=LOOKBACK_DAYS - 1)
    rows = _collect_rows(since.isoformat(), until.isoformat())
    load_by_partition(client, table_id, rows)
    log.info("일상 적재 완료: 총 %d행 (%s ~ %s)", len(rows), since, until)


def _month_windows(since, until):
    windows = []
    cur = since
    while cur <= until:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        windows.append((cur, min(nxt - timedelta(days=1), until)))
        cur = nxt
    return windows


def _minus_months(d, months):
    """d 에서 months 개월 이전 날짜(같은 일자, 말일 보정)."""
    from calendar import monthrange
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def run_backfill(client, table_id, months):
    until = date.today()
    # 메타는 시작일이 현재로부터 37개월을 넘으면 거부(#3018).
    # 정확히 N개월 전에서 5일 버퍼를 둬 안전하게 시작.
    since = _minus_months(until, months) + timedelta(days=5)
    log.info("백필 시작: %s ~ %s (요청 %d개월, 메타 37개월 제한 반영)", since, until, months)
    grand = 0
    for ws, we in _month_windows(since, until):
        rows = _collect_rows(ws.isoformat(), we.isoformat())
        load_by_partition(client, table_id, rows)
        grand += len(rows)
        log.info(">> 구간 %s~%s 적재: %d행 (누적 %d)", ws, we, len(rows), grand)
    log.info("백필 완료: 총 %d행", grand)


def main():
    if "여기에" in META_ACCESS_TOKEN or "여기에" in "".join(AD_ACCOUNT_IDS):
        log.error("META_ACCESS_TOKEN 과 AD_ACCOUNT_IDS 를 먼저 채워주세요.")
        sys.exit(1)
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    table_id = ensure_table(client)
    if BACKFILL_MONTHS > 0:
        run_backfill(client, table_id, BACKFILL_MONTHS)
    else:
        run_daily(client, table_id)


if __name__ == "__main__":
    main()

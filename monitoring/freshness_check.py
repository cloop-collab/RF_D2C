#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freshness_check.py
------------------
핵심 적재 테이블이 "제때" 갱신되고 있는지(신선도) 확인하는 모니터.
각 테이블의 최신 날짜가 허용 지연(days)을 넘으면 '오래됨(STALE)'으로 판정.

- 하나라도 STALE 이면 종료코드 1 → GitHub Actions 실패 → 기본 실패 알림(이메일) 발송.
- SLACK_WEBHOOK_URL 환경변수(선택)가 있으면 요약을 슬랙으로도 전송.
- 읽기 전용(어느 데이터셋도 수정하지 않음).
"""
import os
import sys
import logging

from google.cloud import bigquery

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("freshness_check")

# (표시이름, 테이블, 날짜컬럼, 허용지연일)
#   허용지연일 = 오늘(KST)로부터 최신 날짜가 이 값 이내여야 정상.
CHECKS = [
    ("카페24 매출(시간대)",  "cafe24.rf_cafe24_sales_daily",       "report_date",  2),
    ("카페24 주문",          "cafe24.rf_cafe24_orders",            "report_date",  2),
    ("카페24 유입귀속",      "cafe24.rf_cafe24_order_attribution", "report_date",  2),
    ("메타 광고",            "meta_ads.rf_meta_ads",               "report_date",  2),
    ("네이버 SA",            "naver_ads.rf_naver_sa_ads",          "report_date",  3),
    ("구글 광고(DTS)",       "google_ads_raw.p_ads_CampaignBasicStats_3030273599", "segments_date", 3),
    ("카카오모먼트",         "kakao_moment.rf_kakao_campaign",     "date",         3),
]


def check_one(client, label, table, date_col, max_days):
    q = (f"SELECT MAX(`{date_col}`) AS mx, "
         f"DATE_DIFF(CURRENT_DATE('Asia/Seoul'), MAX(`{date_col}`), DAY) AS lag "
         f"FROM `{BQ_PROJECT}.{table}`")
    try:
        row = list(client.query(q).result())[0]
    except Exception as e:  # noqa: BLE001
        return dict(label=label, table=table, ok=False,
                    detail=f"조회 실패: {str(e)[:120]}")
    if row["mx"] is None:
        return dict(label=label, table=table, ok=False, detail="데이터 없음")
    lag = row["lag"]
    ok = lag is not None and lag <= max_days
    return dict(label=label, table=table, ok=ok,
                detail=f"최신 {row['mx']} (지연 {lag}일 / 허용 {max_days}일)")


def notify_slack(text):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        import requests
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
    except Exception as e:  # noqa: BLE001
        log.warning("슬랙 전송 실패: %s", str(e)[:120])


def main():
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    results = [check_one(client, *c) for c in CHECKS]
    stale = [r for r in results if not r["ok"]]

    for r in results:
        mark = "OK " if r["ok"] else "STALE"
        log.info("[%s] %s — %s (%s)", mark, r["label"], r["detail"], r["table"])

    if stale:
        lines = [f"🔴 데이터 신선도 경고 ({len(stale)}건):"]
        lines += [f"• {r['label']}: {r['detail']}" for r in stale]
        msg = "\n".join(lines)
        log.error(msg)
        notify_slack(msg)
        sys.exit(1)

    notify_slack("✅ 데이터 신선도 정상 (" + ", ".join(r["label"] for r in results) + ")")
    log.info("전체 신선도 정상")


if __name__ == "__main__":
    main()

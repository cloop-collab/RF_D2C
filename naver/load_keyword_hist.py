#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 키워드 성과 이력(전환보고서 정리본) → BigQuery `naver_ads.rf_naver_keyword_hist`.

네이버 검색광고 분석의 요점은 '키워드'. API는 최근 ~94일만 주므로, 그 이전 이력은
어드민 전환보고서(키워드 단위)를 표준 CSV로 정리해 이 테이블에 적재한다.
  분기: 키워드계열(파워링크/쇼핑검색/브랜드검색) + 디스플레이(GFA)는 별도.
  랜딩: URL 기준 자사몰 / 스마트스토어.

입력: naver/keyword_hist_data/*.csv (표준 컬럼, '_' 시작 파일 제외)
  report_date, mall, ad_type, landing, url, campaign, adgroup, keyword, device,
  impressions, clicks, cost, conversions, conversion_value
멱등: report_date 파티션 단위 덮어쓰기(WRITE_TRUNCATE).
"""
import os
import csv
import glob
import datetime
from collections import defaultdict

from google.cloud import bigquery

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
TABLE = f"{PROJECT}.naver_ads.rf_naver_keyword_hist"
SRC_DIR = os.environ.get("NAVER_KW_DIR", "naver/keyword_hist_data")

SF = bigquery.SchemaField
SCHEMA = [
    SF("report_date", "DATE"), SF("mall", "STRING"),
    SF("ad_type", "STRING"), SF("landing", "STRING"), SF("url", "STRING"),
    SF("campaign", "STRING"), SF("adgroup", "STRING"), SF("keyword", "STRING"),
    SF("device", "STRING"),
    SF("impressions", "INT64"), SF("clicks", "INT64"), SF("cost", "FLOAT64"),
    SF("conversions", "INT64"), SF("conversion_value", "FLOAT64"),
    SF("loaded_at", "TIMESTAMP"),
]


def _i(v):
    try: return int(float(str(v).replace(",", "").strip() or 0))
    except (TypeError, ValueError): return 0


def main():
    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    # 테이블 보장(파티션)
    try:
        bq.get_table(TABLE)
    except Exception:  # noqa: BLE001
        t = bigquery.Table(TABLE, schema=SCHEMA)
        t.time_partitioning = bigquery.TimePartitioning(field="report_date")
        t.clustering_fields = ["mall", "ad_type", "landing"]
        bq.create_table(t)
        print(f"[생성] {TABLE}")

    files = [f for f in sorted(glob.glob(os.path.join(SRC_DIR, "*.csv")))
             if not os.path.basename(f).startswith("_")]
    if not files:
        print(f"[info] {SRC_DIR} CSV 없음"); return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    by_date = defaultdict(list)
    total = 0
    for fp in files:
        with open(fp, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d = (r.get("report_date") or "").strip()[:10]
                if not d:
                    continue
                by_date[d].append({
                    "report_date": d, "mall": (r.get("mall") or "").strip().lower(),
                    "ad_type": (r.get("ad_type") or "").strip() or None,
                    "landing": (r.get("landing") or "").strip() or None,
                    "url": (r.get("url") or "").strip() or None,
                    "campaign": (r.get("campaign") or "").strip() or None,
                    "adgroup": (r.get("adgroup") or "").strip() or None,
                    "keyword": (r.get("keyword") or "").strip() or None,
                    "device": (r.get("device") or "").strip() or None,
                    "impressions": _i(r.get("impressions")), "clicks": _i(r.get("clicks")),
                    "cost": float(_i(r.get("cost"))),
                    "conversions": _i(r.get("conversions")),
                    "conversion_value": float(_i(r.get("conversion_value"))),
                    "loaded_at": now,
                })
                total += 1
        print(f"  읽음 {fp}")
    # 날짜 파티션 단위 덮어쓰기(멱등)
    for d, rows in sorted(by_date.items()):
        dest = f"{TABLE}${d.replace('-', '')}"
        bq.load_table_from_json(rows, dest, job_config=bigquery.LoadJobConfig(
            schema=SCHEMA, write_disposition="WRITE_TRUNCATE",
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)).result()
    print(f"적재 완료: {total}행 ({len(by_date)}일) → {TABLE}")


if __name__ == "__main__":
    main()

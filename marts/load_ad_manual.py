#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
광고 어드민 다운로드 RAW → mart.ad_manual 적재(MERGE).

API 이력이 짧은 과거 구간의 광고비/성과를 어드민에서 내려받아 표준 CSV로 정리한 뒤 적재합니다.
통합 뷰(ad_unified_src)가 이 테이블을 UNION하되, 같은 (media,mall,일자)는 API를 우선하고
없을 때만 수동 RAW를 사용합니다(중복 없음).

입력: AD_MANUAL_DIR(기본 marts/manual_ad_data)의 모든 *.csv
CSV 표준 컬럼(헤더 정확히):
  report_date, mall, media, campaign_name, impressions, clicks, cost, conversions, conversion_value
  - report_date: YYYY-MM-DD
  - mall: cloop | sprint
  - media: meta | naver | google | kakao | tiktok ...
  - campaign_id: (선택) 있으면 포함, 없으면 비움
  - 숫자 컬럼의 콤마/원화표시는 자동 제거

멱등: (media, mall, report_date, campaign_name) 기준 MERGE(있으면 갱신, 없으면 삽입).
"""
import os
import csv
import glob
import datetime

from google.cloud import bigquery

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
TABLE = f"{PROJECT}.mart.ad_manual"
STAGING = f"{PROJECT}.mart._ad_manual_staging"
SRC_DIR = os.environ.get("AD_MANUAL_DIR", "marts/manual_ad_data")

NUMCOLS = ["impressions", "clicks", "cost", "conversions", "conversion_value"]


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("₩", "").replace("%", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_rows():
    rows = []
    # '_' 로 시작하는 파일(_TEMPLATE.csv 등)은 예시라 제외
    files = [f for f in sorted(glob.glob(os.path.join(SRC_DIR, "*.csv")))
             if not os.path.basename(f).startswith("_")]
    if not files:
        print(f"[info] {SRC_DIR} 에 CSV 없음 — 적재할 것 없음")
        return rows
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for fp in files:
        with open(fp, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d = (r.get("report_date") or "").strip()[:10]
                if not d:
                    continue
                rows.append({
                    "report_date": d,
                    "mall": (r.get("mall") or "").strip().lower(),
                    "media": (r.get("media") or "").strip().lower(),
                    "campaign_id": (r.get("campaign_id") or "").strip() or None,
                    "campaign_name": (r.get("campaign_name") or "").strip() or None,
                    "landing": (r.get("landing") or "").strip() or None,
                    "impressions": int(_num(r.get("impressions")) or 0),
                    "clicks": int(_num(r.get("clicks")) or 0),
                    "cost": _num(r.get("cost")) or 0.0,
                    "conversions": _num(r.get("conversions")),
                    "conversion_value": _num(r.get("conversion_value")),
                    "source": os.path.basename(fp),
                    "loaded_at": now,
                })
        print(f"  읽음: {fp} (누적 {len(rows)}행)")
    return rows


def main():
    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    rows = read_rows()
    if not rows:
        return
    schema = [
        bigquery.SchemaField("report_date", "DATE"),
        bigquery.SchemaField("mall", "STRING"),
        bigquery.SchemaField("media", "STRING"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
        bigquery.SchemaField("landing", "STRING"),
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("cost", "FLOAT64"),
        bigquery.SchemaField("conversions", "FLOAT64"),
        bigquery.SchemaField("conversion_value", "FLOAT64"),
        bigquery.SchemaField("source", "STRING"),
        bigquery.SchemaField("loaded_at", "TIMESTAMP"),
    ]
    bq.load_table_from_json(rows, STAGING, job_config=bigquery.LoadJobConfig(
        schema=schema, write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)).result()
    print(f"스테이징 적재: {len(rows)}행")

    merge = f"""
    MERGE `{TABLE}` T
    USING `{STAGING}` S
    ON  T.media = S.media AND T.mall = S.mall AND T.report_date = S.report_date
        AND IFNULL(T.campaign_name,'') = IFNULL(S.campaign_name,'')
    WHEN MATCHED THEN UPDATE SET
        campaign_id=S.campaign_id, landing=S.landing,
        impressions=S.impressions, clicks=S.clicks,
        cost=S.cost, conversions=S.conversions, conversion_value=S.conversion_value,
        source=S.source, loaded_at=S.loaded_at
    WHEN NOT MATCHED THEN INSERT ROW
    """
    bq.query(merge).result()
    bq.delete_table(STAGING, not_found_ok=True)
    n = list(bq.query(f"SELECT COUNT(*) c FROM `{TABLE}`").result())[0]["c"]
    print(f"MERGE 완료. mart.ad_manual 총 {n}행")


if __name__ == "__main__":
    main()

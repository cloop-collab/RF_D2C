#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_marts.py
----------------
marts/sql/ 의 .sql 파일을 파일명 순서대로 실행하여 마트(뷰/테이블)를 재생성.
매일 GitHub Actions(marts_daily.yml)에서 원본 적재 이후 실행.

- 뷰(10_*) 먼저 → 물리 테이블(20_*) 순으로 정렬 실행.
- 각 SQL은 CREATE OR REPLACE 이므로 반복 실행 안전.
"""
import os
import glob
import logging

from google.cloud import bigquery

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
SQL_DIR = os.path.join(os.path.dirname(__file__), "sql")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("refresh_marts")


def main():
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    # mart 데이터셋 보장
    ds = bigquery.Dataset(f"{BQ_PROJECT}.mart")
    ds.location = BQ_LOCATION
    client.create_dataset(ds, exists_ok=True)

    files = sorted(glob.glob(os.path.join(SQL_DIR, "*.sql")))
    if not files:
        log.warning("실행할 .sql 파일이 없습니다: %s", SQL_DIR)
        return
    failures = []
    for f in files:
        name = os.path.basename(f)
        with open(f, encoding="utf-8") as fh:
            sql = fh.read()
        try:
            client.query(sql).result()
            log.info("OK: %s", name)
        except Exception as e:  # noqa: BLE001
            log.error("실패: %s → %s", name, str(e)[:300])
            failures.append(name)
    if failures:
        raise SystemExit(f"마트 갱신 일부 실패: {', '.join(failures)}")
    log.info("마트 갱신 완료 (%d개)", len(files))


if __name__ == "__main__":
    main()

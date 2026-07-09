#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_cafe24_views.py
-----------------------
cafe24/sql/ 의 .sql 파일을 파일명 순서대로 실행하여 카페24 파생 뷰를 재생성.
매일 GitHub Actions(cafe24_daily.yml)에서 원본 적재 이후 실행.

- 10_* 주문상태 → 20_* current 통합뷰 → repurchase 뷰 순으로 정렬 실행.
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
log = logging.getLogger("refresh_cafe24_views")


def main():
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
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
            # 파일 내 여러 문장(;) 지원
            for stmt in [s for s in sql.split(";") if s.strip()]:
                client.query(stmt).result()
            log.info("OK: %s", name)
        except Exception as e:  # noqa: BLE001
            log.error("실패: %s → %s", name, str(e)[:300])
            failures.append(name)
    if failures:
        raise SystemExit(f"카페24 뷰 갱신 일부 실패: {', '.join(failures)}")
    log.info("카페24 뷰 갱신 완료 (%d개 파일)", len(files))


if __name__ == "__main__":
    main()

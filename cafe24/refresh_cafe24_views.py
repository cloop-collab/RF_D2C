#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_cafe24_views.py
-----------------------
cafe24/sql/ 의 .sql 파일을 파일명 순서대로 실행하여 카페24 파생 뷰를 재생성.
매일 GitHub Actions(cafe24_daily.yml)에서 원본 적재 이후 실행.

- 10_* 주문상태 → 20_* current 통합뷰 → repurchase 뷰 순으로 정렬 실행.
- 각 SQL은 CREATE OR REPLACE 이므로 반복 실행 안전.

뷰 생성 전 스키마 정렬(align_d0_schemas):
- 20_current_views.sql 은 `본표 SELECT * UNION ALL _d0표 SELECT *` 구조라
  두 표의 컬럼 개수/순서가 항상 동일해야 한다.
- 신규 컬럼은 본표(daily 적재)와 _d0표(15분 intraday 적재)에 붙는 시점이
  달라, 그 사이 틈에서 컬럼 수 불일치(예: 본표 13 / _d0 12)로 뷰 생성이 실패.
- 그래서 뷰를 만들기 직전에 본표↔_d0표의 빠진 컬럼을 NULL 허용으로 상호 추가해
  UNION ALL 이 깨지지 않도록 흡수한다(추가 전용 → 기존 데이터/뷰에 무해).
"""
import os
import glob
import logging

from google.cloud import bigquery

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "cafe24")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
SQL_DIR = os.path.join(os.path.dirname(__file__), "sql")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("refresh_cafe24_views")


def _align_pair(client, base_id, d0_id):
    """본표와 _d0표의 컬럼을 상호 정렬(빠진 컬럼만 NULL 허용으로 추가).

    BigQuery 스키마 업데이트는 '컬럼 추가'만 허용(순서변경/삭제 불가)한다.
    본표·_d0표는 동일한 코드 스키마 리스트를 같은 순서로 append 하므로,
    빠진 컬럼(항상 뒤쪽 접미부)을 채워주면 컬럼 순서가 다시 일치한다.
    """
    base = client.get_table(base_id)
    d0 = client.get_table(d0_id)
    base_names = {f.name for f in base.schema}
    d0_names = {f.name for f in d0.schema}

    add_to_d0 = [bigquery.SchemaField(f.name, f.field_type, mode="NULLABLE")
                 for f in base.schema if f.name not in d0_names]
    add_to_base = [bigquery.SchemaField(f.name, f.field_type, mode="NULLABLE")
                   for f in d0.schema if f.name not in base_names]

    if add_to_d0:
        d0.schema = list(d0.schema) + add_to_d0
        client.update_table(d0, ["schema"])
        log.info("[%s] _d0 스키마 컬럼 추가: %s",
                 d0_id.split(".")[-1], [f.name for f in add_to_d0])
    if add_to_base:
        base.schema = list(base.schema) + add_to_base
        client.update_table(base, ["schema"])
        log.info("[%s] 본표 스키마 컬럼 추가: %s",
                 base_id.split(".")[-1], [f.name for f in add_to_base])

    # 정렬 후에도 위치가 어긋나면(순서 드리프트) 조용히 잘못된 UNION 을 만들지 않도록 경고.
    base = client.get_table(base_id)
    d0 = client.get_table(d0_id)
    b = [(f.name, f.field_type) for f in base.schema]
    z = [(f.name, f.field_type) for f in d0.schema]
    if b != z:
        log.warning("스키마 순서/타입 불일치 잔존(수동 확인 필요): %s vs %s",
                    base_id.split(".")[-1], d0_id.split(".")[-1])


def align_d0_schemas(client):
    """cafe24 데이터셋의 모든 _d0 테이블을 본표와 스키마 정렬."""
    ds_ref = f"{BQ_PROJECT}.{BQ_DATASET}"
    pairs = 0
    for item in client.list_tables(ds_ref):
        if item.table_type != "TABLE":
            continue
        if not item.table_id.endswith("_d0"):
            continue
        base_name = item.table_id[:-3]
        base_id = f"{ds_ref}.{base_name}"
        d0_id = f"{ds_ref}.{item.table_id}"
        try:
            _align_pair(client, base_id, d0_id)
            pairs += 1
        except Exception as e:  # noqa: BLE001  (본표 없음 등은 건너뛰고 계속)
            log.warning("스키마 정렬 건너뜀 %s: %s", item.table_id, str(e)[:200])
    log.info("스키마 정렬 완료: %d개 _d0 쌍", pairs)


def main():
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)

    # 뷰 생성 전, 본표↔_d0 스키마 정렬로 UNION ALL 불일치 예방.
    align_d0_schemas(client)

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

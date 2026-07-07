#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
수신거부(BLOCKLIST) 구글시트 → BigQuery `cafe24.blocklist` 적재.

시트(reg, phone) 를 읽어 휴대폰 숫자만 정규화(phone_digits)해 저장.
CRM 발송대상 추출 시 이 목록의 번호는 제외(suppression)합니다. 알림톡·LMS 공통.

전제: 시트를 서비스계정과 '뷰어' 공유해야 함
      rf-mkt@rf-ads-db-500505.iam.gserviceaccount.com
필수 환경변수: GOOGLE_APPLICATION_CREDENTIALS(SA키), (선택) BLOCKLIST_SHEET_ID/RANGE
"""
import os
import re
import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import bigquery

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
TABLE = f"{PROJECT}.cafe24.blocklist"
SHEET_ID = os.environ.get("BLOCKLIST_SHEET_ID", "1_a4UjVqRek0A615qUPlJDxlCpQJ8Awzl6ImGz0B15ms")
RANGE = os.environ.get("BLOCKLIST_RANGE", "A:B")
KEY = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

SCHEMA = [
    bigquery.SchemaField("reg", "STRING"),
    bigquery.SchemaField("phone", "STRING"),
    bigquery.SchemaField("phone_digits", "STRING"),  # 숫자만(매칭 키)
    bigquery.SchemaField("loaded_at", "TIMESTAMP"),
]


def main():
    creds = service_account.Credentials.from_service_account_file(
        KEY, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=RANGE).execute().get("values", [])
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows, seen = [], set()
    for r in vals[1:]:  # 헤더 제외
        phone = (r[1] if len(r) > 1 else "").strip()
        if not phone:
            continue
        digits = re.sub(r"\D", "", phone)
        if not digits or digits in seen:
            continue
        seen.add(digits)
        rows.append({"reg": (r[0] if r else "").strip(), "phone": phone,
                     "phone_digits": digits, "loaded_at": now})
    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    bq.load_table_from_json(rows, TABLE, job_config=bigquery.LoadJobConfig(
        schema=SCHEMA, write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)).result()
    print(f"수신거부 적재 완료: {len(rows)}건 → {TABLE}")


if __name__ == "__main__":
    main()

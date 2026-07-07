#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CRM 발송 대상 추출 (온디맨드) — 개인정보는 저장하지 않고 CSV로만 출력.

흐름:
  1) [cafe24] 주문에서 조건(상품·기간·최소주문수)을 충족한 member_id 세그먼트 추출  ← 개인정보 없음
  2) 각 회원의 이름·연락처·수신동의를 /customersprivacy 로 '그때그때' 조회        ← 개인정보(온디맨드)
  3) 마케팅 수신동의(sms/news_mail) 필터
  4) send_list CSV 출력 (이름·휴대폰·이메일 + 개인화 변수). BigQuery/코드에 PII 미저장.

입력(환경변수):
  CRM_PRODUCT_NO   : 상품번호(콤마). 이 중 하나라도 구매한 회원.
  CRM_PRODUCT_KW   : 상품명 키워드(부분일치). PRODUCT_NO 와 함께 주면 둘 중 하나라도 매칭.
  CRM_DAYS         : 최근 N일 (기본 30)
  CRM_MALL         : cloop | sprint | all (기본 all)
  CRM_MIN_ORDERS   : 기간 내 최소 주문수 (기본 1)
  CRM_CHANNEL      : sms | email | all  (동의 필터 기준, 기본 all)
  CRM_OUT          : 출력 CSV 경로 (기본 send_list.csv)
자격증명:
  cafe24.oauth_state 의 access_token 사용(파이프라인이 갱신). 만료 시 CAFE24_CLIENT_ID/SECRET 로 refresh.
"""
import os
import sys
import csv
import time
import base64
import datetime

import requests
from google.cloud import bigquery

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
DATASET = os.environ.get("BQ_DATASET", "cafe24")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
MALL_ID = os.environ.get("CAFE24_MALL_ID", "cloop").strip()
CLIENT_ID = os.environ.get("CAFE24_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CAFE24_CLIENT_SECRET", "").strip()
ADMIN = f"https://{MALL_ID}.cafe24api.com/api/v2/admin"

PRODUCT_NO = [x.strip() for x in os.environ.get("CRM_PRODUCT_NO", "").split(",") if x.strip()]
PRODUCT_KW = os.environ.get("CRM_PRODUCT_KW", "").strip()
DAYS = int(os.environ.get("CRM_DAYS") or 30)
MALL = os.environ.get("CRM_MALL", "all").strip() or "all"
MIN_ORDERS = int(os.environ.get("CRM_MIN_ORDERS") or 1)
CHANNEL = os.environ.get("CRM_CHANNEL", "all").strip() or "all"
OUT = os.environ.get("CRM_OUT", "send_list.csv")
SLEEP = float(os.environ.get("CRM_SLEEP") or 0.25)
# 수신동의로 인정할 값(카페24 sms/news_mail 은 보통 'T'/'F')
AGREE = {"T", "Y", "TRUE", "1"}


def get_access_token(bq):
    """oauth_state 의 access_token 사용. 만료 임박 시 client_secret 으로 refresh 후 저장."""
    rows = list(bq.query(
        f"SELECT access_token, refresh_token, access_expires_at "
        f"FROM `{PROJECT}.{DATASET}.oauth_state` WHERE mall_id=@m "
        f"ORDER BY updated_at DESC LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("m", "STRING", MALL_ID)])).result())
    if not rows:
        raise RuntimeError("oauth_state 에 토큰이 없습니다. 파이프라인을 먼저 실행하세요.")
    r = rows[0]
    now = datetime.datetime.now(datetime.timezone.utc)
    exp = r["access_expires_at"]
    if exp and now + datetime.timedelta(seconds=120) < exp:
        return r["access_token"]
    # 만료 임박 → refresh
    if not (CLIENT_ID and CLIENT_SECRET and r["refresh_token"]):
        if r["access_token"]:
            print("[warn] 토큰 만료 임박이나 client_secret 없음 → 저장된 토큰 사용(실패 시 파이프라인 갱신 후 재시도)")
            return r["access_token"]
        raise RuntimeError("토큰 만료 + refresh 불가(CAFE24_CLIENT_ID/SECRET 필요)")
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token",
                         headers={"Authorization": f"Basic {basic}",
                                  "Content-Type": "application/x-www-form-urlencoded"},
                         data={"grant_type": "refresh_token",
                               "refresh_token": r["refresh_token"]}, timeout=30)
    resp.raise_for_status()
    tok = resp.json()
    access = tok["access_token"]
    exp2 = now + datetime.timedelta(seconds=int(tok.get("expires_in", 7200)))
    bq.load_table_from_json([{
        "mall_id": MALL_ID, "access_token": access,
        "refresh_token": tok.get("refresh_token", r["refresh_token"]),
        "access_expires_at": exp2.isoformat(),
        "refresh_expires_at": (now + datetime.timedelta(days=14)).isoformat(),
        "updated_at": now.isoformat()}],
        f"{PROJECT}.{DATASET}.oauth_state",
        job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)).result()
    return access


def build_segment(bq):
    """조건 충족 세그먼트 추출(개인정보 없음): (shop_no, member_id) + 개인화 변수."""
    if not PRODUCT_NO and not PRODUCT_KW:
        raise RuntimeError("CRM_PRODUCT_NO 또는 CRM_PRODUCT_KW 중 하나는 필요합니다.")
    conds, params = [], [
        bigquery.ScalarQueryParameter("days", "INT64", DAYS),
        bigquery.ScalarQueryParameter("minord", "INT64", MIN_ORDERS),
    ]
    prod = []
    if PRODUCT_NO:
        prod.append("i.product_no IN UNNEST(@pno)")
        params.append(bigquery.ArrayQueryParameter("pno", "STRING", PRODUCT_NO))
    if PRODUCT_KW:
        prod.append("i.product_name LIKE @kw")
        params.append(bigquery.ScalarQueryParameter("kw", "STRING", f"%{PRODUCT_KW}%"))
    conds.append("(" + " OR ".join(prod) + ")")
    if MALL != "all":
        conds.append("o.mall = @mall")
        params.append(bigquery.ScalarQueryParameter("mall", "STRING", MALL))
    where = " AND ".join(conds)
    sql = f"""
    WITH matched AS (
      SELECT o.mall, o.shop_no, o.member_id, o.order_id, o.report_date, i.product_name
      FROM `{PROJECT}.{DATASET}.rf_cafe24_orders` o
      JOIN `{PROJECT}.{DATASET}.rf_cafe24_order_items` i
        ON o.order_id = i.order_id AND o.mall = i.mall
      WHERE o.member_id IS NOT NULL AND o.member_id != ''
        AND o.report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL @days DAY)
        AND {where}
    )
    SELECT mall, ANY_VALUE(shop_no) shop_no, member_id,
           MAX(report_date) last_order_date,
           DATE_DIFF(CURRENT_DATE('Asia/Seoul'), MAX(report_date), DAY) days_since,
           COUNT(DISTINCT order_id) order_count,
           STRING_AGG(DISTINCT product_name ORDER BY product_name LIMIT 5) products
    FROM matched
    GROUP BY mall, member_id
    HAVING order_count >= @minord
    ORDER BY last_order_date DESC
    """
    job = bq.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    return [dict(r) for r in job.result()]


def fetch_contact(token, shop_no, member_id):
    """회원 1명의 연락처·수신동의 온디맨드 조회(개인정보). 실패 시 None."""
    r = requests.get(ADMIN + "/customersprivacy",
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"},
                     params={"shop_no": shop_no, "member_id": member_id}, timeout=30)
    if r.status_code != 200:
        return None
    arr = r.json().get("customersprivacy", [])
    return arr[0] if arr else None


def consented(c):
    sms = str(c.get("sms", "")).upper() in AGREE
    mail = str(c.get("news_mail", "")).upper() in AGREE
    if CHANNEL == "sms":
        return sms
    if CHANNEL == "email":
        return mail
    return sms or mail


def main():
    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    token = get_access_token(bq)
    seg = build_segment(bq)
    print(f"세그먼트(조건 충족 회원): {len(seg)}명 — 연락처 온디맨드 조회 시작")
    if len(seg) > 5000:
        print(f"[warn] 대상 {len(seg)}명 — 회원당 1콜이라 {int(len(seg)*SLEEP/60)}분+ 소요. 조건을 좁히는 게 좋습니다.")

    rows, skipped_consent, no_contact = [], 0, 0
    for i, s in enumerate(seg, 1):
        c = fetch_contact(token, s["shop_no"], s["member_id"])
        time.sleep(SLEEP)
        if not c:
            no_contact += 1
            continue
        if not consented(c):
            skipped_consent += 1
            continue
        rows.append({
            "member_id": s["member_id"], "mall": s["mall"],
            "name": c.get("name", ""),
            "cellphone": c.get("cellphone", "") or c.get("phone", ""),
            "email": c.get("email", ""),
            "sms_agree": c.get("sms", ""), "email_agree": c.get("news_mail", ""),
            "products": s["products"],
            "last_order_date": str(s["last_order_date"]),
            "days_since_order": s["days_since"], "order_count": s["order_count"],
        })
        if i % 200 == 0:
            print(f"  ...{i}/{len(seg)} 처리")

    cols = ["member_id", "mall", "name", "cellphone", "email", "sms_agree",
            "email_agree", "products", "last_order_date", "days_since_order", "order_count"]
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"완료: 발송대상 {len(rows)}명 → {OUT}  "
          f"(수신거부 제외 {skipped_consent}, 연락처없음 {no_contact})")


if __name__ == "__main__":
    main()

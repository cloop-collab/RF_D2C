#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CRM 발송대상 추출 (온디맨드) — 개인정보는 저장하지 않고 CSV로만 출력.

흐름:
  1) [cafe24] 주문+주문상품에서 조건 충족 대상 세그먼트 추출               ← 개인정보 없음
       - 회원: (mall, member_id) 단위로 집계 → 고급조건 적용
       - 비회원(게스트): 주문 단위(교차주문 식별 불가)
  2) 대상만 연락처를 '그때그때' 조회
       - 회원: /customersprivacy (이름·휴대폰·이메일·수신동의)
       - 게스트: /orders embed=receivers (수령자 이름·휴대폰)  ※수신동의 플래그 없음
  3) 수신동의 필터(회원) / 게스트는 '동의 별도 확보' 표기
  4) send_list CSV 출력 (BQ/코드에 PII 미저장)

조건(환경변수):
  대상 상품(A):  CRM_PRODUCT_NO(콤마) / CRM_PRODUCT_KW(상품명 부분일치)  [하나 필수]
  옵션:          CRM_OPTION_INCLUDE(옵션값 포함) / CRM_OPTION_EXCLUDE(옵션값 제외)
  제외 상품(B):  CRM_EXCLUDE_PRODUCT_NO / CRM_EXCLUDE_PRODUCT_KW  (B 구매자 제외, 회원만)
  기간:          CRM_DAYS(최근 N일, 기본 30)
  몰:            CRM_MALL(cloop|sprint|all, 기본 all)
  회원 집계:     CRM_MIN_ORDERS(기간내 최소 A주문수, 기본 1) / CRM_MIN_SPEND(기간내 최소 결제액, 기본 0)
  쿠폰:          CRM_COUPON(any|used|notused, 기본 any)  ※주문상품 coupon_discount_price>0 기준
  비회원 포함:   CRM_INCLUDE_GUEST(1=포함, 기본 0)  ※게스트는 수신동의 미확인 → 발송 전 동의 확보 필요
  수신동의(회원):CRM_CHANNEL(sms|email|all, 기본 all)
  출력:          CRM_OUT(기본 send_list.csv)

자격증명: cafe24.oauth_state 의 access_token 사용(파이프라인이 갱신). 만료 시 CAFE24_CLIENT_ID/SECRET 로 refresh.
※교차주문 조건(최소주문수·B제외·결제액합계)은 회원만 적용됨(게스트는 주문 단위라 동일인 식별 불가).
"""
import os
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
OPT_INCLUDE = os.environ.get("CRM_OPTION_INCLUDE", "").strip()
OPT_EXCLUDE = os.environ.get("CRM_OPTION_EXCLUDE", "").strip()
EXCL_NO = [x.strip() for x in os.environ.get("CRM_EXCLUDE_PRODUCT_NO", "").split(",") if x.strip()]
EXCL_KW = os.environ.get("CRM_EXCLUDE_PRODUCT_KW", "").strip()
DAYS = int(os.environ.get("CRM_DAYS") or 30)
MALL = os.environ.get("CRM_MALL", "all").strip() or "all"
MIN_ORDERS = int(os.environ.get("CRM_MIN_ORDERS") or 1)
MIN_SPEND = float(os.environ.get("CRM_MIN_SPEND") or 0)
COUPON = os.environ.get("CRM_COUPON", "any").strip() or "any"
INCLUDE_GUEST = os.environ.get("CRM_INCLUDE_GUEST", "").strip() in ("1", "true", "True")
CHANNEL = os.environ.get("CRM_CHANNEL", "all").strip() or "all"
OUT = os.environ.get("CRM_OUT", "send_list.csv")
SLEEP = float(os.environ.get("CRM_SLEEP") or 0.25)
AGREE = {"T", "Y", "TRUE", "1"}

# 주문상품 원본에서 옵션값/쿠폰할인 추출용 표현식(raw_json JSON 파싱)
OPT_EXPR = "JSON_VALUE(i.raw_json, '$.option_value')"
COUPON_EXPR = "COALESCE(SAFE_CAST(JSON_VALUE(i.raw_json, '$.coupon_discount_price') AS FLOAT64), 0)"


def get_access_token(bq):
    rows = list(bq.query(
        f"SELECT access_token, refresh_token, access_expires_at "
        f"FROM `{PROJECT}.{DATASET}.oauth_state` WHERE mall_id=@m ORDER BY updated_at DESC LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("m", "STRING", MALL_ID)])).result())
    if not rows:
        raise RuntimeError("oauth_state 에 토큰이 없습니다. 파이프라인을 먼저 실행하세요.")
    r = rows[0]
    now = datetime.datetime.now(datetime.timezone.utc)
    if r["access_expires_at"] and now + datetime.timedelta(seconds=120) < r["access_expires_at"]:
        return r["access_token"]
    if not (CLIENT_ID and CLIENT_SECRET and r["refresh_token"]):
        if r["access_token"]:
            print("[warn] 토큰 만료 임박·refresh 불가 → 저장 토큰 사용")
            return r["access_token"]
        raise RuntimeError("토큰 만료 + refresh 불가(CAFE24_CLIENT_ID/SECRET 필요)")
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token",
                         headers={"Authorization": f"Basic {basic}",
                                  "Content-Type": "application/x-www-form-urlencoded"},
                         data={"grant_type": "refresh_token", "refresh_token": r["refresh_token"]},
                         timeout=30)
    resp.raise_for_status()
    tok = resp.json()
    exp2 = now + datetime.timedelta(seconds=int(tok.get("expires_in", 7200)))
    bq.load_table_from_json([{
        "mall_id": MALL_ID, "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", r["refresh_token"]),
        "access_expires_at": exp2.isoformat(),
        "refresh_expires_at": (now + datetime.timedelta(days=14)).isoformat(),
        "updated_at": now.isoformat()}],
        f"{PROJECT}.{DATASET}.oauth_state",
        job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)).result()
    return tok["access_token"]


def _product_clause(prefix, nos, kw, params):
    """상품 매칭 절 생성: 상품번호 IN 또는 상품명 LIKE (하나라도)."""
    parts = []
    if nos:
        parts.append(f"i.product_no IN UNNEST(@{prefix}_no)")
        params.append(bigquery.ArrayQueryParameter(f"{prefix}_no", "STRING", nos))
    if kw:
        parts.append(f"i.product_name LIKE @{prefix}_kw")
        params.append(bigquery.ScalarQueryParameter(f"{prefix}_kw", "STRING", f"%{kw}%"))
    return "(" + " OR ".join(parts) + ")" if parts else None


def build_query():
    if not PRODUCT_NO and not PRODUCT_KW:
        raise RuntimeError("CRM_PRODUCT_NO 또는 CRM_PRODUCT_KW 중 하나는 필요합니다.")
    params = [
        bigquery.ScalarQueryParameter("days", "INT64", DAYS),
        bigquery.ScalarQueryParameter("minord", "INT64", MIN_ORDERS),
        bigquery.ScalarQueryParameter("minspend", "FLOAT64", MIN_SPEND),
    ]
    a_clause = _product_clause("a", PRODUCT_NO, PRODUCT_KW, params)
    # 옵션 필터
    opt = []
    if OPT_INCLUDE:
        opt.append(f"{OPT_EXPR} LIKE @opt_in")
        params.append(bigquery.ScalarQueryParameter("opt_in", "STRING", f"%{OPT_INCLUDE}%"))
    if OPT_EXCLUDE:
        opt.append(f"({OPT_EXPR} IS NULL OR {OPT_EXPR} NOT LIKE @opt_ex)")
        params.append(bigquery.ScalarQueryParameter("opt_ex", "STRING", f"%{OPT_EXCLUDE}%"))
    opt_sql = (" AND " + " AND ".join(opt)) if opt else ""
    mall_sql = ""
    if MALL != "all":
        mall_sql = " AND o.mall = @mall"
        params.append(bigquery.ScalarQueryParameter("mall", "STRING", MALL))
    window = "o.report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL @days DAY)"

    # 쿠폰 조건(회원 집계 HAVING / 게스트 WHERE)
    coupon_having = ""
    if COUPON == "used":
        coupon_having = "AND used_coupon = 1"
    elif COUPON == "notused":
        coupon_having = "AND used_coupon = 0"

    # 제외 상품 B (회원만): 기간내 B 구매 회원 제외
    b_clause = _product_clause("b", EXCL_NO, EXCL_KW, params)
    b_cte = exclude_join = ""
    if b_clause:
        b_cte = f""",
    b_buyers AS (
      SELECT DISTINCT o.mall, o.member_id
      FROM `{PROJECT}.{DATASET}.rf_cafe24_orders` o
      JOIN `{PROJECT}.{DATASET}.rf_cafe24_order_items` i
        ON o.order_id=i.order_id AND o.mall=i.mall
      WHERE {window} AND o.member_id IS NOT NULL AND o.member_id!='' AND {b_clause}
    )"""
        exclude_join = ("LEFT JOIN b_buyers b ON b.mall=a.mall AND b.member_id=a.member_id "
                        "\n  WHERE b.member_id IS NULL")

    sql = f"""
    WITH a_orders AS (       -- 상품A(+옵션) 포함 주문 (주문 단위)
      SELECT o.mall, o.shop_no, o.member_id, o.order_id, o.report_date, o.payment_amount,
             MAX(CASE WHEN {COUPON_EXPR} > 0 THEN 1 ELSE 0 END) AS used_coupon,
             ANY_VALUE(i.product_name) AS a_product
      FROM `{PROJECT}.{DATASET}.rf_cafe24_orders` o
      JOIN `{PROJECT}.{DATASET}.rf_cafe24_order_items` i
        ON o.order_id=i.order_id AND o.mall=i.mall
      WHERE {window} AND {a_clause}{opt_sql}{mall_sql}
      GROUP BY o.mall, o.shop_no, o.member_id, o.order_id, o.report_date, o.payment_amount
    ){b_cte},
    members AS (             -- 회원 집계 + 고급조건
      SELECT a.mall, ANY_VALUE(a.shop_no) shop_no, a.member_id,
             MAX(a.report_date) last_order_date,
             DATE_DIFF(CURRENT_DATE('Asia/Seoul'), MAX(a.report_date), DAY) days_since,
             COUNT(*) order_count, SUM(a.payment_amount) spend,
             MAX(a.used_coupon) used_coupon,
             STRING_AGG(DISTINCT a.a_product ORDER BY a.a_product LIMIT 3) products
      FROM a_orders a
      {exclude_join if b_clause else "WHERE a.member_id IS NOT NULL AND a.member_id!=''"}
      {"AND a.member_id IS NOT NULL AND a.member_id!=''" if b_clause else ""}
      GROUP BY a.mall, a.member_id
      HAVING order_count >= @minord AND spend >= @minspend {coupon_having}
    ),
    guests AS (              -- 비회원: 주문 단위
      SELECT a.mall, a.shop_no, a.order_id AS ref, a.report_date AS last_order_date,
             DATE_DIFF(CURRENT_DATE('Asia/Seoul'), a.report_date, DAY) days_since,
             1 AS order_count, a.payment_amount AS spend, a.used_coupon, a.a_product AS products
      FROM a_orders a
      WHERE (a.member_id IS NULL OR a.member_id='') {coupon_having.replace("used_coupon","a.used_coupon")}
    )
    SELECT 'member' AS customer_type, mall, shop_no, member_id AS ref,
           last_order_date, days_since, order_count, spend, used_coupon, products
    FROM members
    {"UNION ALL SELECT 'guest', mall, shop_no, ref, last_order_date, days_since, order_count, spend, used_coupon, products FROM guests" if INCLUDE_GUEST else ""}
    ORDER BY customer_type, last_order_date DESC
    """
    return sql, params


def fetch_member_contact(token, shop_no, member_id):
    r = requests.get(ADMIN + "/customersprivacy",
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                     params={"shop_no": shop_no, "member_id": member_id}, timeout=30)
    if r.status_code != 200:
        return None
    arr = r.json().get("customersprivacy", [])
    if not arr:
        return None
    c = arr[0]
    return {"name": c.get("name", ""),
            "cellphone": c.get("cellphone", "") or c.get("phone", ""),
            "email": c.get("email", ""),
            "sms": str(c.get("sms", "")).upper(), "news": str(c.get("news_mail", "")).upper()}


def fetch_guest_contact(token, shop_no, order_id):
    r = requests.get(ADMIN + "/orders",
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                     params={"shop_no": shop_no, "order_id": order_id, "embed": "receivers"}, timeout=30)
    if r.status_code != 200:
        return None
    arr = r.json().get("orders", [])
    if not arr:
        return None
    recv = (arr[0].get("receivers") or [{}])[0]
    return {"name": recv.get("name", ""),
            "cellphone": recv.get("cellphone", "") or recv.get("phone", ""),
            "email": "", "sms": "", "news": ""}


def member_consented(c):
    sms = c["sms"] in AGREE
    mail = c["news"] in AGREE
    if CHANNEL == "sms":
        return sms
    if CHANNEL == "email":
        return mail
    return sms or mail


def main():
    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    token = get_access_token(bq)
    sql, params = build_query()
    seg = [dict(r) for r in bq.query(
        sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()]
    n_mem = sum(1 for s in seg if s["customer_type"] == "member")
    n_gst = len(seg) - n_mem
    print(f"세그먼트: 회원 {n_mem}명, 게스트 {n_gst}건 — 연락처 온디맨드 조회 시작")
    if len(seg) > 5000:
        print(f"[warn] 대상 {len(seg)} — 건당 1콜이라 {int(len(seg)*SLEEP/60)}분+ 소요. 조건을 좁히세요.")

    rows, skip_consent, no_contact = [], 0, 0
    for i, s in enumerate(seg, 1):
        if s["customer_type"] == "member":
            c = fetch_member_contact(token, s["shop_no"], s["ref"])
        else:
            c = fetch_guest_contact(token, s["shop_no"], s["ref"])
        time.sleep(SLEEP)
        if not c or not (c["cellphone"] or c["email"]):
            no_contact += 1
            continue
        if s["customer_type"] == "member" and not member_consented(c):
            skip_consent += 1
            continue
        rows.append({
            "customer_type": s["customer_type"], "mall": s["mall"], "ref": s["ref"],
            "name": c["name"], "cellphone": c["cellphone"], "email": c["email"],
            "sms_agree": c["sms"], "email_agree": c["news"],
            "consent_note": "" if s["customer_type"] == "member" else "게스트-수신동의 별도확보필요",
            "products": s["products"], "last_order_date": str(s["last_order_date"]),
            "days_since_order": s["days_since"], "order_count": s["order_count"],
            "spend": s["spend"], "used_coupon": s["used_coupon"],
        })
        if i % 200 == 0:
            print(f"  ...{i}/{len(seg)} 처리")

    cols = ["customer_type", "mall", "ref", "name", "cellphone", "email", "sms_agree",
            "email_agree", "consent_note", "products", "last_order_date",
            "days_since_order", "order_count", "spend", "used_coupon"]
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"완료: 발송대상 {len(rows)} → {OUT}  "
          f"(회원 수신거부 제외 {skip_consent}, 연락처없음 {no_contact})")
    if INCLUDE_GUEST and n_gst:
        print("⚠️ 게스트는 수신동의 플래그가 없습니다. 발송 전 동의 근거를 반드시 확보하세요.")


if __name__ == "__main__":
    main()

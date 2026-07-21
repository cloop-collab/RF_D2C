#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""임시 프로브: Cafe24 /orders 가 구매자 연락처를 (마스킹 없이) 반환하는지 확인.
   ⚠ 실제 개인정보 값은 로깅하지 않음 — 존재/마스킹 여부만 출력. 확인 후 삭제 예정."""
import cafe24_to_bigquery as c
from google.cloud import bigquery

def status(v):
    if v is None:
        return "ABSENT(키없음)"
    s = str(v)
    if s == "":
        return "EMPTY(빈값)"
    return "MASKED(마스킹*)" if "*" in s else f"PRESENT(len={len(s)})"

client = bigquery.Client(project=c.BQ_PROJECT, location=c.BQ_LOCATION)
tm = c.TokenManager(client)

params = {"shop_no": 1, "date_type": "order_date",
          "start_date": "2026-07-08", "end_date": "2026-07-08",
          "embed": "items,receivers,buyer", "limit": 3}
data = c.admin_get("/orders", tm, params=params)
orders = (data.get("orders") if isinstance(data, dict) else None) or []
print("[probe] orders_fetched:", len(orders))
CK = ("buyer", "receiver", "cell", "phone", "name", "email", "mobile")
for i, o in enumerate(orders[:2]):
    keys = sorted([k for k in o.keys() if any(t in k.lower() for t in CK)])
    print(f"[probe] order#{i} contact-ish keys: {keys}")
    for f in ["buyer_name", "buyer_email", "buyer_phone", "buyer_cellphone",
              "cellphone", "phone", "member_id", "member_email"]:
        print(f"[probe]   {f} = {status(o.get(f))}")
    recs = o.get("receivers") or []
    print(f"[probe]   receivers_count: {len(recs)}")
    if recs and isinstance(recs, list) and isinstance(recs[0], dict):
        r = recs[0]
        rk = sorted([k for k in r.keys() if any(t in k.lower() for t in CK)])
        print(f"[probe]   receiver[0] contact keys: {rk}")
        for f in ["name", "cellphone", "phone", "cell"]:
            print(f"[probe]     receiver.{f} = {status(r.get(f))}")
print("[probe] done")

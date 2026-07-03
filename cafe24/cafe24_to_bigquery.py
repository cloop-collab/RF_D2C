#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cafe24_to_bigquery.py
---------------------
카페24(멀티쇼핑몰: 클룹/스프린트) 데이터를 BigQuery 데이터셋 `cafe24`에 적재.

통합 원본 테이블 1벌(몰 구분 `shop_no`/`mall` 컬럼) + 몰별 뷰 자동 생성.
  통계 API (https://ca-api.cafe24data.com) — 일별 집계
    rf_cafe24_sales_daily     ← /sales/times        (일별 매출·주문)
    rf_cafe24_product_sales   ← /products/sales      (상품별 판매)
    rf_cafe24_traffic         ← /visitors/view       (방문자)
    rf_cafe24_traffic_keyword ← /visitpaths/keywords (유입 검색어)
    rf_cafe24_members         ← /members/sales       (회원/비회원 매출)
  관리 API (https://{mall}.cafe24api.com/api/v2/admin) — 원본 건별
    rf_cafe24_orders          ← /orders (embed=items) 주문 (order_id 보존)
    rf_cafe24_order_items     ← 위 주문의 items       (order_id×product_no)
    rf_cafe24_products        ← /products             상품 마스터(스냅샷)
    rf_cafe24_customers       ← /customers            회원 마스터(스냅샷)

설계 관례(메타/네이버와 동일)
  * 모든 행에 raw_json(원본) + ingested_at 보관. 지표 컬럼은 best-effort(.get),
    누락돼도 죽지 않음 → 첫 실행 후 raw_json 보고 컬럼 보강.
  * 날짜 파티션 단위 WRITE_TRUNCATE(덮어쓰기)로 중복 제거.
  * 스키마 변경 시 컬럼 자동 추가.
  * 날짜는 KST 기준.
  * 모드: 일상(LOOKBACK_DAYS) / 백필(BACKFILL_DAYS>0). CAFE24_D0=1 이면 _d0(당일).

OAuth
  * access(2h)/refresh(2주) 토큰을 BigQuery `cafe24.oauth_state`에 저장.
  * refresh 토큰은 사용 시마다 회전(rotate)되므로 갱신 즉시 저장.
  * 최초 1회는 CAFE24_REFRESH_TOKEN(+선택 CAFE24_ACCESS_TOKEN)로 시드.

필수 환경변수
  CAFE24_MALL_ID          : 대표 몰 아이디(관리 API 서브도메인/통계 mall_id)
  CAFE24_CLIENT_ID        : 앱 Client ID
  CAFE24_CLIENT_SECRET    : 앱 Client Secret
  CAFE24_REFRESH_TOKEN    : 최초 시드용 refresh token (첫 실행 이후 불필요)
선택
  CAFE24_SHOPS            : JSON 예 [{"shop_no":1,"mall":"cloop"},...] (없으면 /shops 자동탐색)
  CAFE24_TABLES           : 콤마목록으로 대상 제한 (기본 전체)
  CAFE24_ORDERS_START     : 주문 백필 시작일 YYYY-MM-DD (기본 2018-01-01)
  BQ_PROJECT/BQ_DATASET/BQ_LOCATION, LOOKBACK_DAYS, BACKFILL_DAYS, CAFE24_D0
"""

import os
import sys
import json
import time
import base64
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.cloud import bigquery
from google.api_core.exceptions import NotFound

KST = ZoneInfo("Asia/Seoul")

# ── 설정 ─────────────────────────────────────────────────────────────────────
MALL_ID = os.environ.get("CAFE24_MALL_ID", "").strip()
CLIENT_ID = os.environ.get("CAFE24_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CAFE24_CLIENT_SECRET", "").strip()
SEED_REFRESH = os.environ.get("CAFE24_REFRESH_TOKEN", "").strip()
SEED_ACCESS = os.environ.get("CAFE24_ACCESS_TOKEN", "").strip()

API_VERSION = os.environ.get("CAFE24_API_VERSION", "").strip()  # 비우면 앱 기본버전 사용
ANALYTICS_BASE = "https://ca-api.cafe24data.com"

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or "0")
IS_D0 = os.environ.get("CAFE24_D0", "").strip() in ("1", "true", "True")
ORDERS_START = os.environ.get("CAFE24_ORDERS_START", "2018-01-01")
SLEEP_BETWEEN = float(os.environ.get("SLEEP_BETWEEN") or "0.5")
PAGE_LIMIT = int(os.environ.get("CAFE24_PAGE_LIMIT", "100"))

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "cafe24")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")

# customers(회원 마스터)는 제외: 카페24 /customers 는 member_id/cellphone 필터가 없으면
# 전체 목록 조회를 막음(개인정보 보호). 회원 데이터는 orders.member_id + 통계 members 로 확보.
ALL_TABLES = [
    "sales_daily", "product_sales", "traffic", "traffic_keyword", "members",
    "orders", "products",
]
_env_tables = os.environ.get("CAFE24_TABLES", "").strip()
TABLES = [t.strip() for t in _env_tables.split(",") if t.strip()] or ALL_TABLES

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cafe24_to_bigquery")


def _suffix(name):
    """일상/백필=원본 테이블, D0=..._d0 테이블."""
    return f"rf_cafe24_{name}" + ("_d0" if IS_D0 else "")


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


# ── OAuth (BigQuery 상태 저장) ───────────────────────────────────────────────
OAUTH_TABLE = "oauth_state"


def _oauth_table_id():
    return f"{BQ_PROJECT}.{BQ_DATASET}.{OAUTH_TABLE}"


def _ensure_oauth_table(client):
    ds = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    client.create_dataset(ds, exists_ok=True)
    schema = [
        bigquery.SchemaField("mall_id", "STRING"),
        bigquery.SchemaField("access_token", "STRING"),
        bigquery.SchemaField("refresh_token", "STRING"),
        bigquery.SchemaField("access_expires_at", "TIMESTAMP"),
        bigquery.SchemaField("refresh_expires_at", "TIMESTAMP"),
        bigquery.SchemaField("updated_at", "TIMESTAMP"),
    ]
    try:
        client.get_table(_oauth_table_id())
    except NotFound:
        client.create_table(bigquery.Table(_oauth_table_id(), schema=schema))
        log.info("oauth_state 테이블 생성")


def _read_oauth(client):
    q = (f"SELECT access_token, refresh_token, access_expires_at, refresh_expires_at "
         f"FROM `{_oauth_table_id()}` WHERE mall_id=@m "
         f"ORDER BY updated_at DESC LIMIT 1")
    job = client.query(q, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("m", "STRING", MALL_ID)]))
    for r in job.result():
        return dict(access_token=r["access_token"], refresh_token=r["refresh_token"],
                    access_expires_at=r["access_expires_at"],
                    refresh_expires_at=r["refresh_expires_at"])
    return None


def _write_oauth(client, access, refresh, access_exp, refresh_exp):
    row = {
        "mall_id": MALL_ID, "access_token": access, "refresh_token": refresh,
        "access_expires_at": access_exp.isoformat(),
        "refresh_expires_at": refresh_exp.isoformat() if refresh_exp else None,
        "updated_at": now_utc_iso(),
    }
    cfg = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=[
            bigquery.SchemaField("mall_id", "STRING"),
            bigquery.SchemaField("access_token", "STRING"),
            bigquery.SchemaField("refresh_token", "STRING"),
            bigquery.SchemaField("access_expires_at", "TIMESTAMP"),
            bigquery.SchemaField("refresh_expires_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ])
    client.load_table_from_json([row], _oauth_table_id(), job_config=cfg).result()


def _basic_auth_header():
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _do_refresh(refresh_token):
    url = f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token"
    r = requests.post(url,
                      headers={"Authorization": _basic_auth_header(),
                               "Content-Type": "application/x-www-form-urlencoded"},
                      data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                      timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"토큰 갱신 실패 HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


class TokenManager:
    """BigQuery에 저장된 토큰을 읽고, 만료 임박 시 1회 갱신 후 저장."""

    def __init__(self, client):
        self.client = client
        self._access = None
        self._access_exp = None

    def _load_or_seed(self):
        state = _read_oauth(self.client)
        if state and state.get("refresh_token"):
            return state
        if not SEED_REFRESH:
            raise RuntimeError(
                "저장된 토큰이 없고 CAFE24_REFRESH_TOKEN 시드도 없습니다. "
                "최초 1회 refresh token을 환경변수로 넣어주세요.")
        log.info("저장 토큰 없음 → 시드 refresh token으로 초기화")
        # 시드 access는 만료된 것으로 간주하여 즉시 갱신 유도
        return {"access_token": SEED_ACCESS or None,
                "refresh_token": SEED_REFRESH,
                "access_expires_at": None, "refresh_expires_at": None}

    def get_access_token(self):
        if self._access and self._access_exp and \
                datetime.now(timezone.utc) + timedelta(seconds=300) < self._access_exp:
            return self._access

        state = self._load_or_seed()
        access = state.get("access_token")
        access_exp = state.get("access_expires_at")
        need = (not access) or (not access_exp) or \
            (datetime.now(timezone.utc) + timedelta(seconds=300) >= access_exp)

        if need:
            tok = _do_refresh(state["refresh_token"])
            access = tok["access_token"]
            refresh = tok.get("refresh_token", state["refresh_token"])
            access_exp = datetime.now(timezone.utc) + timedelta(
                seconds=int(tok.get("expires_in", 7200)))
            refresh_exp = datetime.now(timezone.utc) + timedelta(days=14)
            # 회전된 refresh 토큰을 반드시 즉시 저장
            _write_oauth(self.client, access, refresh, access_exp, refresh_exp)
            log.info("access token 갱신·저장 (만료 %s)", access_exp.isoformat())

        self._access, self._access_exp = access, access_exp
        return access


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _request(method, url, token_mgr, params=None, max_retries=5):
    last = ""
    for attempt in range(max_retries):
        headers = {"Authorization": f"Bearer {token_mgr.get_access_token()}",
                   "Content-Type": "application/json"}
        if API_VERSION:
            headers["X-Cafe24-Api-Version"] = API_VERSION
        r = requests.request(method, url, params=params, headers=headers, timeout=60)
        if r.status_code == 200:
            return r.json()
        last = r.text[:400]
        if r.status_code == 401:  # access 만료 등 → 강제 갱신 후 재시도
            token_mgr._access_exp = datetime.now(timezone.utc)
            time.sleep(1)
            continue
        if r.status_code in (429, 500, 502, 503):
            wait = min(60, 2 ** attempt * 2)
            log.warning("HTTP %s → %ds 후 재시도 (%d/%d)", r.status_code, wait,
                        attempt + 1, max_retries)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {last}")
    raise RuntimeError(f"재시도 초과: {last}")


def admin_get(path, token_mgr, params=None):
    url = f"https://{MALL_ID}.cafe24api.com/api/v2/admin{path}"
    return _request("GET", url, token_mgr, params=params)


def analytics_get(path, token_mgr, params=None):
    url = f"{ANALYTICS_BASE}{path}"
    return _request("GET", url, token_mgr, params=params)


def paginate_admin(path, token_mgr, key, base_params, max_pages=100000):
    """offset 기반 페이지네이션으로 전체 레코드 수집."""
    offset, out = 0, []
    for _ in range(max_pages):
        params = dict(base_params)
        params.update({"limit": PAGE_LIMIT, "offset": offset})
        data = admin_get(path, token_mgr, params=params)
        chunk = data.get(key, []) if isinstance(data, dict) else []
        out.extend(chunk)
        if len(chunk) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        time.sleep(SLEEP_BETWEEN)
    return out


# ── BigQuery 적재 공통 ───────────────────────────────────────────────────────
def ensure_table(client, table, schema, partition_field=None, cluster=None):
    ds = bigquery.Dataset(f"{BQ_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    client.create_dataset(ds, exists_ok=True)
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{table}"
    try:
        t = client.get_table(table_id)
        existing = {f.name for f in t.schema}
        missing = [f for f in schema if f.name not in existing]
        if missing:
            t.schema = list(t.schema) + missing
            client.update_table(t, ["schema"])
            log.info("[%s] 스키마 컬럼 추가: %s", table, [f.name for f in missing])
    except NotFound:
        t = bigquery.Table(table_id, schema=schema)
        if partition_field:
            t.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=partition_field)
        if cluster:
            t.clustering_fields = cluster
        client.create_table(t)
        log.info("[%s] 테이블 생성", table)
    return table_id


def load_by_partition(client, table_id, schema, rows, date_field):
    """date_field 값(YYYY-MM-DD)별로 파티션 덮어쓰기."""
    if not rows:
        log.info("적재할 행 없음: %s", table_id)
        return
    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        by_date[r.get(date_field)].append(r)
    for d, drows in sorted(by_date.items()):
        if not d:
            continue
        dest = f"{table_id}${d.replace('-', '')}"
        cfg = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
        client.load_table_from_json(drows, dest, job_config=cfg).result()
        log.info("적재(덮어쓰기) %s %s: %d행", table_id.split('.')[-1], d, len(drows))


def load_replace(client, table_id, schema, rows):
    """테이블 전체 덮어쓰기(마스터 스냅샷)."""
    cfg = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
    client.load_table_from_json(rows or [], table_id, job_config=cfg).result()
    log.info("적재(전체교체) %s: %d행", table_id.split('.')[-1], len(rows or []))


def ensure_views(client, base_table, shops):
    """통합 테이블에서 몰별 뷰 생성 (mall 필터)."""
    for s in shops:
        vname = f"{base_table}_{s['mall']}"
        view_id = f"{BQ_PROJECT}.{BQ_DATASET}.{vname}"
        sql = (f"SELECT * FROM `{BQ_PROJECT}.{BQ_DATASET}.{base_table}` "
               f"WHERE mall = '{s['mall']}'")
        view = bigquery.Table(view_id)
        view.view_query = sql
        try:
            client.delete_table(view_id, not_found_ok=True)
            client.create_table(view)
            log.info("뷰 준비: %s", vname)
        except Exception as e:  # noqa: BLE001
            log.warning("뷰 생성 실패 %s: %s", vname, str(e)[:120])


# ── 숫자 파싱 ────────────────────────────────────────────────────────────────
def _f(rec, *keys):
    for k in keys:
        v = rec.get(k) if isinstance(rec, dict) else None
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _s(rec, *keys):
    for k in keys:
        v = rec.get(k) if isinstance(rec, dict) else None
        if v not in (None, ""):
            return str(v)
    return None


# ── 날짜 유틸 ────────────────────────────────────────────────────────────────
def date_range(since_s, until_s):
    out, cur, end = [], date.fromisoformat(since_s), date.fromisoformat(until_s)
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def window_range(since_s, until_s, days=90):
    """긴 구간을 days 단위 (since, until) 윈도우로 분할 (주문 API 3개월 제한 대응)."""
    cur, end = date.fromisoformat(since_s), date.fromisoformat(until_s)
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        yield cur.isoformat(), nxt.isoformat()
        cur = nxt + timedelta(days=1)


def compute_span():
    until = datetime.now(KST).date()
    if IS_D0:
        return until.isoformat(), until.isoformat()
    span = BACKFILL_DAYS if BACKFILL_DAYS > 0 else LOOKBACK_DAYS
    since = until - timedelta(days=span - 1)
    return since.isoformat(), until.isoformat()


# ── 쇼핑몰(shop_no) 목록 ─────────────────────────────────────────────────────
def _mall_label(shop_no, name):
    name = (name or "").lower()
    if "sprint" in name or "스프린트" in name:
        return "sprint"
    if "cloop" in name or "클룹" in name:
        return "cloop"
    return f"shop{shop_no}"


def resolve_shops(token_mgr):
    env = os.environ.get("CAFE24_SHOPS", "").strip()
    if env:
        return json.loads(env)
    try:
        data = admin_get("/shops", token_mgr, params={"limit": 100})
        shops = data.get("shops", []) if isinstance(data, dict) else []
        out = [{"shop_no": int(s.get("shop_no", 1)),
                "mall": _mall_label(s.get("shop_no", 1),
                                    s.get("shop_name") or s.get("shop_name_en"))}
               for s in shops]
        if out:
            log.info("쇼핑몰 자동탐색: %s", out)
            return out
    except Exception as e:  # noqa: BLE001
        log.warning("쇼핑몰 자동탐색 실패(%s) → 기본값 사용", str(e)[:120])
    return [{"shop_no": 1, "mall": "cloop"}, {"shop_no": 2, "mall": "sprint"}]


# ── 통계 API 수집 ────────────────────────────────────────────────────────────
ANALYTICS_SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),
    bigquery.SchemaField("shop_no", "INT64"),
    bigquery.SchemaField("mall", "STRING"),
    bigquery.SchemaField("dim1", "STRING"),   # 상품/검색어 등 세부 차원(있으면)
    bigquery.SchemaField("dim2", "STRING"),
    bigquery.SchemaField("val1", "FLOAT64"),  # best-effort 대표 수치(매출/건수)
    bigquery.SchemaField("val2", "FLOAT64"),
    bigquery.SchemaField("raw_json", "STRING"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]

# 통계 테이블 → (엔드포인트, 응답 배열 키 후보, 대표 dim/val 필드 후보)
ANALYTICS_SPEC = {
    "sales_daily":    ("/sales/times",        ["times", "sales", "data", "count"],
                       [], ["order_amount", "buy_amount", "sales_amount"], ["order_count", "buy_count"]),
    "product_sales":  ("/products/sales",     ["products", "sales", "data", "count"],
                       ["product_no", "product_name"], ["sales_amount", "buy_amount"], ["sales_count", "buy_count"]),
    "traffic":        ("/visitors/view",      ["visitors", "view", "data", "count"],
                       [], ["visit_count", "unique_visitors"], ["first_visit_count", "re_visit_count"]),
    "traffic_keyword": ("/visitpaths/keywords", ["keywords", "data", "count"],
                        ["keyword"], ["visit_count"], ["order_count"]),
    "members":        ("/members/sales",      ["members", "sales", "data", "count"],
                       [], ["member_order_amount", "member_buy_amount"], ["nonmember_order_amount", "nonmember_buy_amount"]),
}


def _extract_list(data, key_candidates):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for k in key_candidates:
        v = data.get(k)
        if isinstance(v, list):
            return v
    # 첫 번째 list 값 사용
    for v in data.values():
        if isinstance(v, list):
            return v
    return []


def collect_analytics(name, token_mgr, shops, since_s, until_s):
    path, keys, dims, val1_keys, val2_keys = ANALYTICS_SPEC[name]
    rows = []
    for s in shops:
        params = {"mall_id": MALL_ID, "shop_no": s["shop_no"],
                  "start_date": since_s, "end_date": until_s}
        try:
            data = analytics_get(path, token_mgr, params=params)
        except RuntimeError as e:
            log.warning("[%s/%s] 통계 조회 실패: %s", name, s["mall"], str(e)[:150])
            continue
        for rec in _extract_list(data, keys):
            if not isinstance(rec, dict):
                continue
            d = _s(rec, "date", "report_date", "std_date") or until_s
            d = d[:10]
            rows.append({
                "report_date": d, "shop_no": s["shop_no"], "mall": s["mall"],
                "dim1": _s(rec, *dims) if dims else None,
                "dim2": _s(rec, "product_name") if name == "product_sales" else None,
                "val1": _f(rec, *val1_keys),
                "val2": _f(rec, *val2_keys),
                "raw_json": json.dumps(rec, ensure_ascii=False),
                "ingested_at": now_utc_iso(),
            })
        time.sleep(SLEEP_BETWEEN)
    return rows


# ── 관리 API 수집 ────────────────────────────────────────────────────────────
ORDERS_SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),      # 주문일(KST)
    bigquery.SchemaField("order_id", "STRING"),
    bigquery.SchemaField("shop_no", "INT64"),
    bigquery.SchemaField("mall", "STRING"),
    bigquery.SchemaField("member_id", "STRING"),
    bigquery.SchemaField("order_status", "STRING"),
    bigquery.SchemaField("payment_amount", "FLOAT64"),
    bigquery.SchemaField("order_amount", "FLOAT64"),
    bigquery.SchemaField("currency", "STRING"),
    bigquery.SchemaField("ordered_at", "TIMESTAMP"),
    bigquery.SchemaField("raw_json", "STRING"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]

ORDER_ITEMS_SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),
    bigquery.SchemaField("order_id", "STRING"),
    bigquery.SchemaField("shop_no", "INT64"),
    bigquery.SchemaField("mall", "STRING"),
    bigquery.SchemaField("product_no", "STRING"),
    bigquery.SchemaField("variant_code", "STRING"),
    bigquery.SchemaField("product_name", "STRING"),
    bigquery.SchemaField("quantity", "INT64"),
    bigquery.SchemaField("product_price", "FLOAT64"),
    bigquery.SchemaField("raw_json", "STRING"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]

MASTER_PRODUCTS_SCHEMA = [
    bigquery.SchemaField("snapshot_date", "DATE"),
    bigquery.SchemaField("shop_no", "INT64"),
    bigquery.SchemaField("mall", "STRING"),
    bigquery.SchemaField("product_no", "STRING"),
    bigquery.SchemaField("product_name", "STRING"),
    bigquery.SchemaField("price", "FLOAT64"),
    bigquery.SchemaField("retail_price", "FLOAT64"),
    bigquery.SchemaField("display", "STRING"),
    bigquery.SchemaField("selling", "STRING"),
    bigquery.SchemaField("created_date", "STRING"),
    bigquery.SchemaField("raw_json", "STRING"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]

MASTER_CUSTOMERS_SCHEMA = [
    bigquery.SchemaField("snapshot_date", "DATE"),
    bigquery.SchemaField("shop_no", "INT64"),
    bigquery.SchemaField("mall", "STRING"),
    bigquery.SchemaField("member_id", "STRING"),
    bigquery.SchemaField("group_no", "STRING"),
    bigquery.SchemaField("created_date", "STRING"),
    bigquery.SchemaField("raw_json", "STRING"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]


def _order_date(rec):
    v = _s(rec, "order_date", "payment_date", "created_date")
    return v[:10] if v else None


def collect_orders_window(token_mgr, s, w_since, w_until):
    """단일 shop + 단일 90일 구간의 주문/주문상품 수집 (페이지네이션)."""
    orders, items = [], []
    offset = 0
    while True:
        params = {"shop_no": s["shop_no"], "start_date": w_since,
                  "end_date": w_until, "date_type": "order_date",
                  "embed": "items", "limit": PAGE_LIMIT, "offset": offset}
        try:
            data = admin_get("/orders", token_mgr, params=params)
        except RuntimeError as e:
            log.warning("[orders/%s %s~%s] 실패: %s", s["mall"], w_since,
                        w_until, str(e)[:150])
            break
        chunk = data.get("orders", []) if isinstance(data, dict) else []
        for o in chunk:
            d = _order_date(o) or w_until
            oid = _s(o, "order_id")
            orders.append({
                "report_date": d, "order_id": oid,
                "shop_no": s["shop_no"], "mall": s["mall"],
                "member_id": _s(o, "member_id"),
                "order_status": _s(o, "order_status", "status"),
                "payment_amount": _f(o, "payment_amount", "actual_payment_amount"),
                "order_amount": _f(o, "order_price_amount", "order_amount"),
                "currency": _s(o, "currency"),
                "ordered_at": _s(o, "order_date", "payment_date"),
                "raw_json": json.dumps(o, ensure_ascii=False),
                "ingested_at": now_utc_iso(),
            })
            for it in (o.get("items") or []):
                items.append({
                    "report_date": d, "order_id": oid,
                    "shop_no": s["shop_no"], "mall": s["mall"],
                    "product_no": _s(it, "product_no"),
                    "variant_code": _s(it, "variant_code"),
                    "product_name": _s(it, "product_name"),
                    "quantity": int(_f(it, "quantity") or 0),
                    "product_price": _f(it, "product_price", "payment_amount"),
                    "raw_json": json.dumps(it, ensure_ascii=False),
                    "ingested_at": now_utc_iso(),
                })
        if len(chunk) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        time.sleep(SLEEP_BETWEEN)
    return orders, items


def collect_products(token_mgr, shops):
    snap = datetime.now(KST).date().isoformat()
    rows = []
    for s in shops:
        recs = paginate_admin("/products", token_mgr, "products",
                              {"shop_no": s["shop_no"]})
        for p in recs:
            rows.append({
                "snapshot_date": snap, "shop_no": s["shop_no"], "mall": s["mall"],
                "product_no": _s(p, "product_no"),
                "product_name": _s(p, "product_name"),
                "price": _f(p, "price"),
                "retail_price": _f(p, "retail_price"),
                "display": _s(p, "display"),
                "selling": _s(p, "selling"),
                "created_date": _s(p, "created_date"),
                "raw_json": json.dumps(p, ensure_ascii=False),
                "ingested_at": now_utc_iso(),
            })
    return rows


def collect_customers(token_mgr, shops):
    snap = datetime.now(KST).date().isoformat()
    rows = []
    for s in shops:
        recs = paginate_admin("/customers", token_mgr, "customers",
                              {"shop_no": s["shop_no"]})
        for c in recs:
            rows.append({
                "snapshot_date": snap, "shop_no": s["shop_no"], "mall": s["mall"],
                "member_id": _s(c, "member_id"),
                "group_no": _s(c, "group_no"),
                "created_date": _s(c, "created_date"),
                "raw_json": json.dumps(c, ensure_ascii=False),
                "ingested_at": now_utc_iso(),
            })
    return rows


# ── 오케스트레이션 ───────────────────────────────────────────────────────────
def run_analytics(client, token_mgr, shops, since_s, until_s):
    for name in ["sales_daily", "product_sales", "traffic", "traffic_keyword", "members"]:
        if name not in TABLES:
            continue
        try:
            table = _suffix(name)
            table_id = ensure_table(client, table, ANALYTICS_SCHEMA,
                                    partition_field="report_date",
                                    cluster=["mall", "dim1"])
            rows = collect_analytics(name, token_mgr, shops, since_s, until_s)
            load_by_partition(client, table_id, ANALYTICS_SCHEMA, rows, "report_date")
            if not IS_D0:
                ensure_views(client, table, shops)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 수집/적재 실패, 건너뜀: %s", name, str(e)[:200])


def run_orders(client, token_mgr, shops, since_s, until_s):
    o_table = _suffix("orders")
    i_table = _suffix("order_items")
    o_id = ensure_table(client, o_table, ORDERS_SCHEMA,
                        partition_field="report_date", cluster=["mall", "order_id"])
    i_id = ensure_table(client, i_table, ORDER_ITEMS_SCHEMA,
                        partition_field="report_date", cluster=["mall", "product_no"])
    # 90일 구간마다(양쪽 몰 합쳐) 즉시 적재 → 대량 백필 시 메모리 한정·부분 안전
    for w_since, w_until in window_range(since_s, until_s, days=90):
        w_orders, w_items = [], []
        for s in shops:
            o, i = collect_orders_window(token_mgr, s, w_since, w_until)
            w_orders += o
            w_items += i
        load_by_partition(client, o_id, ORDERS_SCHEMA, w_orders, "report_date")
        load_by_partition(client, i_id, ORDER_ITEMS_SCHEMA, w_items, "report_date")
    if not IS_D0:
        ensure_views(client, o_table, shops)
        ensure_views(client, i_table, shops)


def run_products(client, token_mgr, shops):
    table = _suffix("products")
    tid = ensure_table(client, table, MASTER_PRODUCTS_SCHEMA,
                       partition_field="snapshot_date", cluster=["mall", "product_no"])
    rows = collect_products(token_mgr, shops)
    # 스냅샷: 오늘 파티션만 덮어쓰기
    load_by_partition(client, tid, MASTER_PRODUCTS_SCHEMA, rows, "snapshot_date")
    if not IS_D0:
        ensure_views(client, table, shops)


def run_customers(client, token_mgr, shops):
    table = _suffix("customers")
    tid = ensure_table(client, table, MASTER_CUSTOMERS_SCHEMA,
                       partition_field="snapshot_date", cluster=["mall", "member_id"])
    rows = collect_customers(token_mgr, shops)
    load_by_partition(client, tid, MASTER_CUSTOMERS_SCHEMA, rows, "snapshot_date")
    if not IS_D0:
        ensure_views(client, table, shops)


def main():
    missing = [k for k, v in {
        "CAFE24_MALL_ID": MALL_ID, "CAFE24_CLIENT_ID": CLIENT_ID,
        "CAFE24_CLIENT_SECRET": CLIENT_SECRET}.items() if not v]
    if missing:
        log.error("환경변수 누락: %s", ", ".join(missing))
        sys.exit(1)

    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    _ensure_oauth_table(client)
    token_mgr = TokenManager(client)
    token_mgr.get_access_token()  # 최초 갱신·저장 검증

    shops = resolve_shops(token_mgr)
    since_s, until_s = compute_span()
    orders_since = ORDERS_START if (BACKFILL_DAYS <= 0 and not IS_D0
                                    and "orders" in TABLES and _wants_full_orders()) \
        else since_s
    log.info("모드=%s, 구간 %s~%s, 대상=%s, shops=%s",
             "D0" if IS_D0 else ("백필" if BACKFILL_DAYS > 0 else "일상"),
             since_s, until_s, TABLES, [s["mall"] for s in shops])

    failures = []
    sections = []
    if any(t in TABLES for t in
           ["sales_daily", "product_sales", "traffic", "traffic_keyword", "members"]):
        sections.append(("analytics", lambda: run_analytics(client, token_mgr, shops, since_s, until_s)))
    if "orders" in TABLES:
        sections.append(("orders", lambda: run_orders(client, token_mgr, shops, orders_since, until_s)))
    if "products" in TABLES:
        sections.append(("products", lambda: run_products(client, token_mgr, shops)))
    if "customers" in TABLES:
        sections.append(("customers", lambda: run_customers(client, token_mgr, shops)))

    for label, fn in sections:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 섹션 실패, 계속 진행: %s", label, str(e)[:200])
            failures.append(label)

    if failures:
        log.warning("완료(일부 실패): %s", ", ".join(failures))
    else:
        log.info("완료(전체 성공)")


def _wants_full_orders():
    """CAFE24_ORDERS_FULL=1 이면 주문을 ORDERS_START부터 전체 백필."""
    return os.environ.get("CAFE24_ORDERS_FULL", "").strip() in ("1", "true", "True")


if __name__ == "__main__":
    main()

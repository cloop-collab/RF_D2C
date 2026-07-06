"""
Kakao Moment -> BigQuery 데이터 파이프라인
카카오모먼트 광고계정에서 일자별 성과를 가져와 BigQuery에 적재합니다.
소재형식(creative_format)으로 디스플레이(DA)와 메시지(CRM)를 구분해 별도 테이블에 넣습니다.

  1) rf_kakao_moment  : 디스플레이(비즈보드, DA) — creative_format 에 'image' 포함, 전체 성과
  2) rf_kakao_message : 메시지(CRM)            — creative_format 에 'message' 포함, 비용만

수집 단위: 광고계정 × 소재형식(creative_format) × 일자  (계정 리포트 + dimension=CREATIVE_FORMAT)

실행 모드:
  backfill : 최근 N일치(BACKFILL_DAYS, 기본 365) — API 31일 제한이라 자동 분할 호출
  daily    : 어제 하루치 (매일 새벽)

인증(2026-07 보완): 카카오 토큰은 ~6시간이면 만료되므로 **refresh token 자동갱신** 사용.
  - KAKAO_REST_API_KEY(client_id) + KAKAO_REFRESH_TOKEN(시드) → 매 실행 시 access token 발급.
  - access/refresh 를 BigQuery kakao_moment.oauth_state 에 저장(회전 대응, 카페24와 동일).
  - (하위호환) refresh 세팅이 없고 KAKAO_ACCESS_TOKEN 만 있으면 그 정적 토큰을 사용.

사용법:  python kakao_to_bigquery.py [backfill|daily]
설정은 모두 환경변수(GitHub Secrets)에서 읽습니다.

API 문서: https://developers.kakao.com/docs/ko/kakaomoment/report
  - 엔드포인트: GET https://apis.moment.kakao.com/openapi/v4/adAccounts/report
  - 헤더: Authorization: Bearer <BUSINESS_ACCESS_TOKEN>, adAccountId: <ID>
  - 파라미터: adAccountId, start/end(yyyyMMdd, 31일 이내), metricsGroup=BASIC,
              dimension=CREATIVE_FORMAT, timeUnit=DAY
  - 응답: data[].{start, end, dimensions.creative_format, metrics.{imp,click,ctr,cost}}
"""
import os
import sys
import datetime
import zoneinfo
import requests
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
DATASET = os.environ.get("BQ_DATASET", "kakao_moment")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")

KAKAO_API_BASE = os.environ.get("KAKAO_API_BASE", "https://apis.moment.kakao.com")
REPORT_PATH = "/openapi/v4/adAccounts/report"
KAUTH_TOKEN = "https://kauth.kakao.com/oauth/business/token"  # 비즈니스 토큰(카카오모먼트)

# 인증 설정
REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
SEED_REFRESH = os.environ.get("KAKAO_REFRESH_TOKEN", "").strip()
STATIC_ACCESS = os.environ.get("KAKAO_ACCESS_TOKEN", "").strip()  # 하위호환

TB_DA = "rf_kakao_moment"    # 디스플레이(DA)
TB_MSG = "rf_kakao_message"  # 메시지(CRM)
OAUTH_TABLE = "oauth_state"

SF = bigquery.SchemaField

# ---------------- 테이블 스키마 ----------------

# 디스플레이(DA): 전체 성과
DA_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("creative_format", "STRING"),   # 카카오 원본 값 (예: IMAGE BANNER, IMAGE NATIVE)
    SF("ad_type", "STRING"),           # 파생: DISPLAY / OTHER
    SF("impressions", "INTEGER"),
    SF("clicks", "INTEGER"),
    SF("cost", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# 메시지(CRM): 비용만 추적 (노출/클릭은 메시지 성격상 별도 지표그룹, 기본은 비용만 안정 수집)
MESSAGE_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("creative_format", "STRING"),
    SF("ad_type", "STRING"),           # 파생: MESSAGE
    SF("cost", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# ---------------- OAuth (refresh token 자동갱신, BQ 저장) ----------------

def _oauth_table_id():
    return f"{PROJECT}.{DATASET}.{OAUTH_TABLE}"


def ensure_dataset(bq):
    ds = bigquery.Dataset(f"{PROJECT}.{DATASET}")
    ds.location = LOCATION
    bq.create_dataset(ds, exists_ok=True)


def _ensure_oauth_table(bq):
    schema = [
        SF("app_key", "STRING"), SF("access_token", "STRING"),
        SF("refresh_token", "STRING"), SF("access_expires_at", "TIMESTAMP"),
        SF("updated_at", "TIMESTAMP"),
    ]
    try:
        bq.get_table(_oauth_table_id())
    except NotFound:
        bq.create_table(bigquery.Table(_oauth_table_id(), schema=schema))
        print(f"[oauth] {OAUTH_TABLE} 테이블 생성")


def _read_oauth(bq):
    q = (f"SELECT access_token, refresh_token, access_expires_at "
         f"FROM `{_oauth_table_id()}` WHERE app_key=@k ORDER BY updated_at DESC LIMIT 1")
    job = bq.query(q, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("k", "STRING", REST_API_KEY)]))
    for r in job.result():
        return dict(access_token=r["access_token"], refresh_token=r["refresh_token"],
                    access_expires_at=r["access_expires_at"])
    return None


def _write_oauth(bq, access, refresh, access_exp):
    row = {"app_key": REST_API_KEY, "access_token": access, "refresh_token": refresh,
           "access_expires_at": access_exp.isoformat(),
           "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    cfg = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=[
            SF("app_key", "STRING"), SF("access_token", "STRING"),
            SF("refresh_token", "STRING"), SF("access_expires_at", "TIMESTAMP"),
            SF("updated_at", "TIMESTAMP"),
        ])
    bq.load_table_from_json([row], _oauth_table_id(), job_config=cfg).result()


def _refresh(refresh_token):
    data = {"grant_type": "refresh_token", "client_id": REST_API_KEY,
            "refresh_token": refresh_token}
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET
    r = requests.post(KAUTH_TOKEN, data=data, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        raise RuntimeError(f"카카오 토큰 갱신 실패 HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def get_access_token(bq):
    """refresh 세팅이 있으면 자동갱신, 없으면 정적 KAKAO_ACCESS_TOKEN(하위호환)."""
    if REST_API_KEY:
        _ensure_oauth_table(bq)
        state = _read_oauth(bq)
        if not state or not state.get("refresh_token"):
            if not SEED_REFRESH:
                if STATIC_ACCESS:
                    print("[oauth] refresh 없음 → 정적 KAKAO_ACCESS_TOKEN 사용(만료 주의)")
                    return STATIC_ACCESS
                raise RuntimeError("KAKAO_REFRESH_TOKEN 시드 또는 KAKAO_ACCESS_TOKEN 필요")
            state = {"access_token": None, "refresh_token": SEED_REFRESH,
                     "access_expires_at": None}
        access, exp = state.get("access_token"), state.get("access_expires_at")
        now = datetime.datetime.now(datetime.timezone.utc)
        if (not access) or (not exp) or now + datetime.timedelta(seconds=300) >= exp:
            tok = _refresh(state["refresh_token"])
            access = tok["access_token"]
            refresh = tok.get("refresh_token", state["refresh_token"])
            exp = now + datetime.timedelta(seconds=int(tok.get("expires_in", 21599)))
            _write_oauth(bq, access, refresh, exp)
            print(f"[oauth] access token 갱신·저장 (만료 {exp.isoformat()})")
        return access
    if STATIC_ACCESS:
        return STATIC_ACCESS
    raise RuntimeError("KAKAO_REST_API_KEY(+REFRESH_TOKEN) 또는 KAKAO_ACCESS_TOKEN 필요")


# ---------------- 공통 함수 ----------------

def date_range(mode):
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).date()  # 한국시간 기준
    if mode == "backfill":
        days = int(os.environ.get("BACKFILL_DAYS") or 365)
        return today - datetime.timedelta(days=days), today - datetime.timedelta(days=1)
    if mode == "daily":
        d = today - datetime.timedelta(days=1)
        return d, d
    raise ValueError(f"알 수 없는 모드: {mode}")


def date_chunks(start, end, max_days=31):
    """카카오 리포트는 start~end 31일 이내만 허용 → 구간 분할."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + datetime.timedelta(days=max_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + datetime.timedelta(days=1)


def _clean(v):
    return v.strip() if isinstance(v, str) else v


def _norm_date(v):
    """'20260702' 또는 '2026-07-02' → 'YYYY-MM-DD' (BigQuery DATE용)."""
    s = str(v or "").strip().replace(".", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def classify(creative_format):
    """creative_format 으로 광고유형 판별: message→MESSAGE, image→DISPLAY, 그 외→OTHER."""
    cf = (creative_format or "").lower()
    if "message" in cf:
        return "MESSAGE"
    if "image" in cf:
        return "DISPLAY"
    return "OTHER"


def fetch_report(access_token, account_id, start, end):
    """카카오모먼트 계정 리포트(소재형식 분해) → 표준 dict 리스트."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "adAccountId": str(account_id),
    }
    params = {
        "adAccountId": str(account_id),
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "metricsGroup": "BASIC",
        "dimension": "CREATIVE_FORMAT",
        "timeUnit": "DAY",
    }
    resp = requests.get(KAKAO_API_BASE + REPORT_PATH, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    rows = []
    for item in payload.get("data", []):
        dim = item.get("dimensions", {})
        met = item.get("metrics", {})
        rows.append({
            "date": _norm_date(item.get("start")),
            "ad_account_id": str(account_id),
            "creative_format": dim.get("creative_format", "") or "",
            "impressions": int(met.get("imp", 0) or 0),
            "clicks": int(met.get("click", 0) or 0),
            "cost": float(met.get("cost", 0) or 0),
        })
    return rows


def ensure_table(bq, table, schema):
    table_id = f"{PROJECT}.{DATASET}.{table}"
    try:
        bq.get_table(table_id)
    except NotFound:
        t = bigquery.Table(table_id, schema=schema)
        t.time_partitioning = bigquery.TimePartitioning(field="date")
        bq.create_table(t)
        print(f"[BigQuery] 테이블 생성: {table_id}")


def delete_range(bq, table, start, end):
    table_id = f"{PROJECT}.{DATASET}.{table}"
    bq.query(f"DELETE FROM `{table_id}` WHERE date BETWEEN '{start}' AND '{end}'").result()


def load_rows(bq, table, schema, rows):
    table_id = f"{PROJECT}.{DATASET}.{table}"
    if not rows:
        print(f"[{table}] 적재할 데이터 없음")
        return
    cfg = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")
    bq.load_table_from_json(rows, table_id, job_config=cfg).result()
    print(f"[{table}] {len(rows)}행 적재 완료")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RUN_MODE", "daily")
    start, end = date_range(mode)
    print(f"=== 모드={mode}  기간={start}~{end} ===")

    account_ids = [a.strip() for a in os.environ["KAKAO_AD_ACCOUNT_IDS"].split(",") if a.strip()]

    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    ensure_dataset(bq)
    access_token = get_access_token(bq)   # refresh 자동갱신(또는 정적 하위호환)
    ensure_table(bq, TB_DA, DA_SCHEMA)
    ensure_table(bq, TB_MSG, MESSAGE_SCHEMA)

    now = datetime.datetime.utcnow().isoformat()
    da_rows, msg_rows = [], []

    for acc in account_ids:
        for c_start, c_end in date_chunks(start, end):
            print(f"[계정 {acc}] 리포트 수집 {c_start}~{c_end}")
            for r in fetch_report(access_token, acc, c_start, c_end):
                ad_type = classify(r["creative_format"])
                base = {
                    "date": r["date"], "ad_account_id": r["ad_account_id"],
                    "creative_format": r["creative_format"], "ad_type": ad_type,
                    "loaded_at": now,
                }
                if ad_type == "MESSAGE":
                    msg_rows.append({**base, "cost": r["cost"]})
                else:
                    da_rows.append({
                        **base,
                        "impressions": r["impressions"], "clicks": r["clicks"], "cost": r["cost"],
                    })

    # 확정 테이블: 해당 기간 삭제 후 적재 (재실행 안전 = 멱등)
    delete_range(bq, TB_DA, start, end)
    delete_range(bq, TB_MSG, start, end)
    load_rows(bq, TB_DA, DA_SCHEMA, da_rows)
    load_rows(bq, TB_MSG, MESSAGE_SCHEMA, msg_rows)
    print("=== 완료 ===")


if __name__ == "__main__":
    main()

"""
Kakao Moment -> BigQuery 데이터 파이프라인
카카오모먼트 광고계정에서 일자별 성과를 여러 단위(입도)로 가져와 BigQuery에 적재합니다.

  1) rf_kakao_moment   : 소재형식(DA) — creative_format 에 'image' 포함, 노출/클릭/비용
  2) rf_kakao_message  : 소재형식(메시지) — creative_format 에 'message' 포함, 비용
  3) rf_kakao_campaign : 캠페인 단위 — campaign_id/name, 노출/클릭/비용 (타 매체와 통일)
  4) rf_kakao_adgroup  : 광고그룹 단위 — adgroup_id/name(+campaign_id), 노출/클릭/비용

수집 방식:
  - 소재형식: adAccounts/report + dimension=CREATIVE_FORMAT
  - 캠페인  : adAccounts/report + level=CAMPAIGN  (이름은 캠페인 목록 API로 매핑)
  - 광고그룹: 캠페인별 광고그룹 목록 API로 ID 수집 → adGroups/report (adGroupId 최대 40개 배치)

실행 모드:
  backfill : 최근 N일치(BACKFILL_DAYS, 기본 365) — API 31일 제한이라 자동 분할 호출
  daily    : 어제 하루치 (매일 새벽)

인증: 카카오모먼트는 '비즈니스 인증' access token(refresh 없음). 정적 KAKAO_ACCESS_TOKEN 우선 사용,
      매일 사용하면 만료되지 않음. (하위호환: REST_API_KEY+REFRESH 시드 방식도 지원)

사용법:  python kakao_to_bigquery.py [backfill|daily]
설정은 모두 환경변수(GitHub Secrets)에서 읽습니다.

API 문서: https://developers.kakao.com/docs/ko/kakaomoment/report
  - GET https://apis.moment.kakao.com/openapi/v4/adAccounts/report  (level=CAMPAIGN | dimension=CREATIVE_FORMAT)
  - GET https://apis.moment.kakao.com/openapi/v4/adGroups/report     (adGroupId 최대 40)
  - GET https://apis.moment.kakao.com/openapi/v4/campaigns           (캠페인 목록)
  - GET https://apis.moment.kakao.com/openapi/v4/adGroups            (광고그룹 목록, campaignId 필수)
  - 헤더: Authorization: Bearer <BUSINESS_ACCESS_TOKEN>, adAccountId: <ID>
  - 다건 조회는 앱당 5초에 1회 제한 → 호출 전 대기 + 429 재시도
"""
import os
import sys
import time
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
ADGROUP_REPORT_PATH = "/openapi/v4/adGroups/report"
CAMPAIGN_LIST_PATH = "/openapi/v4/campaigns"
ADGROUP_LIST_PATH = "/openapi/v4/adGroups"
KAUTH_TOKEN = "https://kauth.kakao.com/oauth/business/token"  # 비즈니스 토큰(카카오모먼트)

# 레이트리밋: 리포트(다건 조회)는 앱당 5초 1회 → 넉넉히 6초. 목록/관리 API는 완화(1.5초).
SLEEP_REPORT = 6.0
SLEEP_LIST = 1.5
ADGROUP_BATCH = 40  # adGroups/report 는 한 번에 최대 40개 ID

# 인증 설정
REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
SEED_REFRESH = os.environ.get("KAKAO_REFRESH_TOKEN", "").strip()
STATIC_ACCESS = os.environ.get("KAKAO_ACCESS_TOKEN", "").strip()  # 하위호환

TB_DA = "rf_kakao_moment"      # 소재형식(DA)
TB_MSG = "rf_kakao_message"    # 소재형식(메시지)
TB_CAMP = "rf_kakao_campaign"  # 캠페인 단위
TB_ADG = "rf_kakao_adgroup"    # 광고그룹 단위
OAUTH_TABLE = "oauth_state"

SF = bigquery.SchemaField

# ---------------- 테이블 스키마 ----------------

# 소재형식 DA: 전체 성과
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

# 소재형식 메시지: 비용
MESSAGE_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("creative_format", "STRING"),
    SF("ad_type", "STRING"),           # 파생: MESSAGE
    SF("cost", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# 캠페인 단위 (타 매체와 통일: campaign_id/name)
CAMPAIGN_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("campaign_id", "STRING"),
    SF("campaign_name", "STRING"),
    SF("impressions", "INTEGER"),
    SF("clicks", "INTEGER"),
    SF("cost", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# 광고그룹 단위
ADGROUP_SCHEMA = [
    SF("date", "DATE"),
    SF("ad_account_id", "STRING"),
    SF("campaign_id", "STRING"),
    SF("adgroup_id", "STRING"),
    SF("adgroup_name", "STRING"),
    SF("impressions", "INTEGER"),
    SF("clicks", "INTEGER"),
    SF("cost", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

# ---------------- OAuth (비즈니스 토큰 / refresh 하위호환, BQ 저장) ----------------

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
    """카카오 비즈니스 토큰은 refresh 토큰이 없음(재발급 방식) → 정적 KAKAO_ACCESS_TOKEN
    (비즈니스 access token)을 우선 사용. 매일 사용하면 만료되지 않음."""
    if STATIC_ACCESS:
        return STATIC_ACCESS
    if REST_API_KEY:
        _ensure_oauth_table(bq)
        state = _read_oauth(bq)
        if not state or not state.get("refresh_token"):
            if not SEED_REFRESH:
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


def _batches(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


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


def kakao_get(access_token, account_id, path, params, sleep_before=SLEEP_REPORT):
    """카카오모먼트 GET 공통 호출: 호출 전 대기(레이트리밋) + 429/5xx 재시도 → JSON."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "adAccountId": str(account_id),
    }
    time.sleep(sleep_before)
    resp = None
    for attempt in range(6):
        resp = requests.get(KAKAO_API_BASE + path, headers=headers, params=params, timeout=60)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 6 + attempt * 2
            print(f"[rate-limit/{resp.status_code}] {wait}s 대기 후 재시도 ({attempt+1}/6) {path}")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


# ---------------- 목록(엔티티) 조회 : id → name 매핑 ----------------

def fetch_campaign_list(access_token, account_id):
    """계정의 캠페인 목록 → {campaign_id(str): name}. (config 미지정=전체 상태)"""
    payload = kakao_get(access_token, account_id, CAMPAIGN_LIST_PATH, {}, sleep_before=SLEEP_LIST)
    out = {}
    for c in payload.get("content", []):
        cid = str(c.get("id"))
        out[cid] = c.get("name", "") or ""
    return out


def fetch_adgroup_meta(access_token, account_id, campaign_ids):
    """캠페인별 광고그룹 목록 → {adgroup_id(str): (campaign_id, name)}."""
    meta = {}
    for cid in campaign_ids:
        payload = kakao_get(access_token, account_id, ADGROUP_LIST_PATH,
                            {"campaignId": cid}, sleep_before=SLEEP_LIST)
        for g in payload.get("content", []):
            meta[str(g.get("id"))] = (cid, g.get("name", "") or "")
    return meta


# ---------------- 리포트 조회 ----------------

def fetch_creative_format_report(access_token, account_id, start, end):
    """소재형식(creative_format) 분해 리포트."""
    params = {
        "adAccountId": str(account_id),
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
        "metricsGroup": "BASIC", "dimension": "CREATIVE_FORMAT", "timeUnit": "DAY",
    }
    payload = kakao_get(access_token, account_id, REPORT_PATH, params)
    rows = []
    for item in payload.get("data", []):
        dim = item.get("dimensions", {})
        met = item.get("metrics", {})
        rows.append({
            "date": _norm_date(item.get("start")),
            "creative_format": dim.get("creative_format", "") or "",
            "impressions": int(met.get("imp", 0) or 0),
            "clicks": int(met.get("click", 0) or 0),
            "cost": float(met.get("cost", 0) or 0),
        })
    return rows


def fetch_campaign_report(access_token, account_id, start, end):
    """캠페인 단위 리포트 (level=CAMPAIGN). dimensions.campaign_id 로 식별."""
    params = {
        "adAccountId": str(account_id),
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
        "metricsGroup": "BASIC", "level": "CAMPAIGN", "timeUnit": "DAY",
    }
    payload = kakao_get(access_token, account_id, REPORT_PATH, params)
    rows = []
    for item in payload.get("data", []):
        dim = item.get("dimensions", {})
        met = item.get("metrics", {})
        rows.append({
            "date": _norm_date(item.get("start")),
            "campaign_id": str(dim.get("campaign_id", "") or ""),
            "impressions": int(met.get("imp", 0) or 0),
            "clicks": int(met.get("click", 0) or 0),
            "cost": float(met.get("cost", 0) or 0),
        })
    return rows


def fetch_adgroup_report(access_token, account_id, adgroup_ids, start, end):
    """광고그룹 단위 리포트. adGroupId 최대 40개 배치. dimensions.ad_group_id 로 식별."""
    rows = []
    for batch in _batches(adgroup_ids, ADGROUP_BATCH):
        params = {
            "adAccountId": str(account_id),
            "adGroupId": ",".join(batch),
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "metricsGroup": "BASIC", "timeUnit": "DAY",
        }
        payload = kakao_get(access_token, account_id, ADGROUP_REPORT_PATH, params)
        for item in payload.get("data", []):
            dim = item.get("dimensions", {})
            met = item.get("metrics", {})
            agid = dim.get("ad_group_id", dim.get("adgroup_id", "")) or ""
            rows.append({
                "date": _norm_date(item.get("start")),
                "adgroup_id": str(agid),
                "impressions": int(met.get("imp", 0) or 0),
                "clicks": int(met.get("click", 0) or 0),
                "cost": float(met.get("cost", 0) or 0),
            })
    return rows


# ---------------- BigQuery 적재 ----------------

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
    access_token = get_access_token(bq)
    for tb, sc in ((TB_DA, DA_SCHEMA), (TB_MSG, MESSAGE_SCHEMA),
                   (TB_CAMP, CAMPAIGN_SCHEMA), (TB_ADG, ADGROUP_SCHEMA)):
        ensure_table(bq, tb, sc)

    now = datetime.datetime.utcnow().isoformat()
    da_rows, msg_rows, camp_rows, adg_rows = [], [], [], []

    for acc in account_ids:
        # 1) 엔티티 이름 매핑(기간 무관, 계정당 1회)
        camp_names = fetch_campaign_list(access_token, acc)
        adg_meta = fetch_adgroup_meta(access_token, acc, list(camp_names.keys()))
        adgroup_ids = list(adg_meta.keys())
        print(f"[계정 {acc}] 캠페인 {len(camp_names)}개 · 광고그룹 {len(adgroup_ids)}개")

        for c_start, c_end in date_chunks(start, end):
            print(f"[계정 {acc}] 리포트 수집 {c_start}~{c_end}")
            # 2) 소재형식(creative_format)
            for r in fetch_creative_format_report(access_token, acc, c_start, c_end):
                ad_type = classify(r["creative_format"])
                base = {"date": r["date"], "ad_account_id": acc,
                        "creative_format": r["creative_format"], "ad_type": ad_type,
                        "loaded_at": now}
                if ad_type == "MESSAGE":
                    msg_rows.append({**base, "cost": r["cost"]})
                else:
                    da_rows.append({**base, "impressions": r["impressions"],
                                    "clicks": r["clicks"], "cost": r["cost"]})
            # 3) 캠페인
            for r in fetch_campaign_report(access_token, acc, c_start, c_end):
                camp_rows.append({
                    "date": r["date"], "ad_account_id": acc,
                    "campaign_id": r["campaign_id"],
                    "campaign_name": camp_names.get(r["campaign_id"], ""),
                    "impressions": r["impressions"], "clicks": r["clicks"],
                    "cost": r["cost"], "loaded_at": now,
                })
            # 4) 광고그룹
            if adgroup_ids:
                for r in fetch_adgroup_report(access_token, acc, adgroup_ids, c_start, c_end):
                    cid, gname = adg_meta.get(r["adgroup_id"], ("", ""))
                    adg_rows.append({
                        "date": r["date"], "ad_account_id": acc,
                        "campaign_id": cid, "adgroup_id": r["adgroup_id"],
                        "adgroup_name": gname,
                        "impressions": r["impressions"], "clicks": r["clicks"],
                        "cost": r["cost"], "loaded_at": now,
                    })

    # 확정 테이블: 해당 기간 삭제 후 적재 (재실행 안전 = 멱등)
    for tb, sc, rows in ((TB_DA, DA_SCHEMA, da_rows), (TB_MSG, MESSAGE_SCHEMA, msg_rows),
                         (TB_CAMP, CAMPAIGN_SCHEMA, camp_rows), (TB_ADG, ADGROUP_SCHEMA, adg_rows)):
        delete_range(bq, tb, start, end)
        load_rows(bq, tb, sc, rows)
    print("=== 완료 ===")


if __name__ == "__main__":
    main()

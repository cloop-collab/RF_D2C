"""
Google Ads -> BigQuery 데이터 파이프라인
MCC(관리자 계정) 아래 모든 광고 계정에서 두 종류의 리포트를 가져와 BigQuery에 적재합니다.

  1) campaign_daily : 모든 캠페인의 일별 x 기기별 성과
  2) keyword_daily  : 검색광고(GSA)의 키워드별 x 기기별 성과

실행 모드:
  backfill : 최근 365일치 한 번에 적재 (최초 1회)
  daily    : 어제 하루치 (매일 새벽)
  intraday : 오늘 하루치 (매시간, 덮어쓰기)

사용법:  python pipeline.py [backfill|daily|intraday]
설정은 모두 환경변수(GitHub Secrets)에서 읽습니다.
"""
import os
import sys
import datetime
import zoneinfo
from google.ads.googleads.client import GoogleAdsClient
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
DATASET = os.environ.get("BQ_DATASET", "google_ads")

SF = bigquery.SchemaField

# ---------------- 리포트 정의 ----------------

CAMPAIGN_SCHEMA = [
    SF("date", "DATE"), SF("customer_id", "STRING"), SF("customer_name", "STRING"),
    SF("campaign_id", "STRING"), SF("campaign_name", "STRING"),
    SF("campaign_status", "STRING"), SF("channel_type", "STRING"),
    SF("device", "STRING"),
    SF("impressions", "INTEGER"), SF("clicks", "INTEGER"), SF("cost", "FLOAT"),
    SF("conversions", "FLOAT"), SF("conversions_value", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]

KEYWORD_SCHEMA = [
    SF("date", "DATE"), SF("customer_id", "STRING"), SF("customer_name", "STRING"),
    SF("campaign_id", "STRING"), SF("campaign_name", "STRING"),
    SF("ad_group_id", "STRING"), SF("ad_group_name", "STRING"),
    SF("keyword_text", "STRING"), SF("match_type", "STRING"),
    SF("device", "STRING"),
    SF("impressions", "INTEGER"), SF("clicks", "INTEGER"), SF("cost", "FLOAT"),
    SF("conversions", "FLOAT"), SF("conversions_value", "FLOAT"),
    SF("loaded_at", "TIMESTAMP"),
]


def campaign_query(start, end):
    return f"""
        SELECT
          segments.date, segments.device,
          customer.id,
          campaign.id, campaign.name, campaign.status,
          campaign.advertising_channel_type,
          metrics.impressions, metrics.clicks, metrics.cost_micros,
          metrics.conversions, metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
    """


def campaign_map(r, cname, now):
    return {
        "date": r.segments.date,
        "customer_id": str(r.customer.id),
        "customer_name": cname,
        "campaign_id": str(r.campaign.id),
        "campaign_name": r.campaign.name,
        "campaign_status": r.campaign.status.name,
        "channel_type": r.campaign.advertising_channel_type.name,
        "device": r.segments.device.name,
        "impressions": int(r.metrics.impressions),
        "clicks": int(r.metrics.clicks),
        "cost": r.metrics.cost_micros / 1_000_000,
        "conversions": float(r.metrics.conversions),
        "conversions_value": float(r.metrics.conversions_value),
        "loaded_at": now,
    }


def keyword_query(start, end):
    # 검색광고(GSA) 캠페인의 키워드만
    return f"""
        SELECT
          segments.date, segments.device,
          customer.id,
          campaign.id, campaign.name,
          ad_group.id, ad_group.name,
          ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          metrics.impressions, metrics.clicks, metrics.cost_micros,
          metrics.conversions, metrics.conversions_value
        FROM keyword_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.advertising_channel_type = 'SEARCH'
    """


def keyword_map(r, cname, now):
    return {
        "date": r.segments.date,
        "customer_id": str(r.customer.id),
        "customer_name": cname,
        "campaign_id": str(r.campaign.id),
        "campaign_name": r.campaign.name,
        "ad_group_id": str(r.ad_group.id),
        "ad_group_name": r.ad_group.name,
        "keyword_text": r.ad_group_criterion.keyword.text,
        "match_type": r.ad_group_criterion.keyword.match_type.name,
        "device": r.segments.device.name,
        "impressions": int(r.metrics.impressions),
        "clicks": int(r.metrics.clicks),
        "cost": r.metrics.cost_micros / 1_000_000,
        "conversions": float(r.metrics.conversions),
        "conversions_value": float(r.metrics.conversions_value),
        "loaded_at": now,
    }


REPORTS = [
    {"table": "campaign_daily", "schema": CAMPAIGN_SCHEMA,
     "query": campaign_query, "map": campaign_map},
    {"table": "keyword_daily", "schema": KEYWORD_SCHEMA,
     "query": keyword_query, "map": keyword_map},
]

# ---------------- 공통 함수 ----------------

def date_range(mode):
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).date()  # 한국시간 기준
    if mode == "backfill":
        return today - datetime.timedelta(days=365), today - datetime.timedelta(days=1)
    if mode == "daily":
        d = today - datetime.timedelta(days=1)
        return d, d
    if mode == "intraday":
        return today, today
    raise ValueError(f"알 수 없는 모드: {mode}")


def get_child_accounts(client, mcc_id):
    svc = client.get_service("GoogleAdsService")
    query = """
        SELECT customer_client.id, customer_client.descriptive_name,
               customer_client.manager, customer_client.status
        FROM customer_client
        WHERE customer_client.status = 'ENABLED'
    """
    accounts = []
    for batch in svc.search_stream(customer_id=mcc_id, query=query):
        for row in batch.results:
            cc = row.customer_client
            if not cc.manager:
                accounts.append((str(cc.id), cc.descriptive_name))
    return accounts


def fetch(client, customer_id, cname, report, start, end):
    svc = client.get_service("GoogleAdsService")
    now = datetime.datetime.utcnow().isoformat()
    rows = []
    for batch in svc.search_stream(customer_id=customer_id, query=report["query"](start, end)):
        for r in batch.results:
            rows.append(report["map"](r, cname, now))
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

    mcc_id = os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"]
    client = GoogleAdsClient.load_from_env()
    bq = bigquery.Client(project=PROJECT)

    for rep in REPORTS:
        ensure_table(bq, rep["table"], rep["schema"])

    accounts = get_child_accounts(client, mcc_id)
    print(f"연결된 광고 계정 {len(accounts)}개 발견")

    # 리포트별로 모든 계정 데이터를 모은다
    collected = {rep["table"]: [] for rep in REPORTS}
    failed = False
    for cid, cname in accounts:
        for rep in REPORTS:
            try:
                rows = fetch(client, cid, cname, rep, start, end)
                collected[rep["table"]].extend(rows)
                print(f"  - {cid} {cname} [{rep['table']}]: {len(rows)}행")
            except Exception as e:
                print(f"  ! {cid} {cname} [{rep['table']}] 오류: {e}")
                failed = True

    if failed and mode != "backfill":
        print("[중단] 일부 조회 실패. 데이터 유실 방지를 위해 삭제 없이 종료합니다.")
        sys.exit(1)

    for rep in REPORTS:
        delete_range(bq, rep["table"], start, end)
        load_rows(bq, rep["table"], rep["schema"], collected[rep["table"]])

    print("=== 완료 ===")


if __name__ == "__main__":
    main()

-- naver_ads.rf_naver_keyword_unified
-- 네이버 검색광고 키워드 단위 통합 뷰 (API 최신 + RAW 과거, 날짜×몰 dedup)
--
-- 목적: 키워드 분석용 연속 이력(2026-01-01~현재)을 한 곳에 제공.
--   - API(rf_naver_sa_ads, level='keyword')  = 정본. 2026-04-05~현재. ID/roas 등 풍부.
--   - RAW(rf_naver_keyword_hist)             = 과거 보완(관리자 CSV export). 2026-01-01~06-29.
--   - dedup: 같은 (report_date, mall)이 API에 있으면 API 우선, 없을 때만 RAW.
--            (LEFT JOIN anti-join — BQ가 CTE 상관 서브쿼리 미지원이라 NOT EXISTS 대신)
--
-- 컬럼 주의:
--   - ad_type : RAW는 원본값(파워링크/쇼핑검색/브랜드검색/신제품검색). API는 campaign_name에서 파생(best-effort).
--   - landing : 자사몰/스마트스토어. API=lp_type, RAW=landing. 매출 채널 매칭용.
--   - device  : RAW(모바일/PC)만. API는 캠페인 타게팅 접미사(_MO/_PC)와 의미가 달라 NULL 처리.
--   - 브랜드검색 cost=0 : 네이버 전환보고서에 브랜드검색 정액비용 미배분(별도 비용보고서 필요) — RAW/API 공통.
CREATE OR REPLACE VIEW `rf-ads-db-500505.naver_ads.rf_naver_keyword_unified` AS
WITH api AS (
  SELECT
    report_date,
    CASE account WHEN 'CLOOP' THEN 'cloop' WHEN 'SPRINT' THEN 'sprint' ELSE LOWER(account) END AS mall,
    CASE
      WHEN campaign_name LIKE '%파워링크%'   THEN '파워링크'
      WHEN campaign_name LIKE '%브랜드검색%' THEN '브랜드검색'
      WHEN campaign_name LIKE '%스토어%'
        OR campaign_name LIKE '%SBA%'
        OR campaign_name LIKE '%쇼핑%'
        OR REGEXP_CONTAINS(campaign_name, r'_SA_') THEN '쇼핑검색'
      ELSE '기타'
    END AS ad_type,
    lp_type AS landing,
    campaign_name AS campaign,
    adgroup_name  AS adgroup,
    keyword,
    CAST(NULL AS STRING) AS device,
    impressions,
    clicks,
    cost,
    CAST(conversions AS FLOAT64) AS conversions,
    conversion_value,
    'api' AS source
  FROM `rf-ads-db-500505.naver_ads.rf_naver_sa_ads`
  WHERE level = 'keyword'
),
api_days AS (
  SELECT DISTINCT report_date, mall FROM api
),
raw_hist AS (
  SELECT
    r.report_date,
    r.mall,
    r.ad_type,
    r.landing,
    r.campaign,
    r.adgroup,
    r.keyword,
    r.device,
    r.impressions,
    r.clicks,
    r.cost,
    CAST(r.conversions AS FLOAT64) AS conversions,
    r.conversion_value,
    'raw' AS source
  FROM `rf-ads-db-500505.naver_ads.rf_naver_keyword_hist` r
  LEFT JOIN api_days a
    ON a.report_date = r.report_date AND a.mall = r.mall
  WHERE a.report_date IS NULL   -- API에 없는 (날짜×몰)만 RAW로 보완
)
SELECT * FROM api
UNION ALL
SELECT * FROM raw_hist;

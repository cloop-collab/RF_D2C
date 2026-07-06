-- 매체 통합 소스 뷰: 메타 + 네이버 + 구글(DTS)을 공통 컬럼으로 통일 (캠페인 단위)
-- 몰 구분: 메타=account_id, 네이버=account, 구글=customer_id
CREATE OR REPLACE VIEW `rf-ads-db-500505.mart.ad_unified_src` AS
WITH meta AS (
  SELECT report_date,
    CASE
      WHEN account_id IN ('1462607070849777','793134085895227','3589083851393515') THEN 'cloop'
      WHEN account_id = '3342733785912061' THEN 'sprint'
      ELSE 'unknown' END AS mall,
    'meta' AS media, campaign_id, ANY_VALUE(campaign_name) AS campaign_name,
    SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(spend) AS cost,
    SUM(web_purchase_count) AS conversions, SUM(web_purchase_value) AS conversion_value
  FROM `rf-ads-db-500505.meta_ads.rf_meta_ads`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY report_date, mall, campaign_id
),
naver AS (
  SELECT report_date, LOWER(account) AS mall, 'naver' AS media, campaign_id,
    ANY_VALUE(campaign_name) AS campaign_name,
    SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(cost) AS cost,
    SUM(conversions) AS conversions, SUM(conversion_value) AS conversion_value
  FROM `rf-ads-db-500505.naver_ads.rf_naver_sa_ads`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR) AND level='campaign'
  GROUP BY report_date, mall, campaign_id
),
g_stats AS (
  SELECT segments_date AS report_date,
    CASE customer_id WHEN 2580015098 THEN 'cloop' WHEN 2082026590 THEN 'sprint' ELSE 'unknown' END AS mall,
    campaign_id,
    SUM(metrics_impressions) AS impressions,
    SUM(metrics_clicks) AS clicks,
    SUM(metrics_cost_micros)/1e6 AS cost,
    SUM(metrics_conversions) AS conversions,
    SUM(metrics_conversions_value) AS conversion_value
  FROM `rf-ads-db-500505.google_ads_raw.p_ads_CampaignBasicStats_3030273599`
  WHERE segments_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY report_date, mall, campaign_id
),
g_name AS (
  SELECT campaign_id, ANY_VALUE(campaign_name) AS campaign_name
  FROM `rf-ads-db-500505.google_ads_raw.p_ads_Campaign_3030273599` GROUP BY campaign_id
),
google AS (
  SELECT s.report_date, s.mall, 'google' AS media, CAST(s.campaign_id AS STRING) AS campaign_id,
    n.campaign_name, s.impressions, s.clicks, s.cost, s.conversions, s.conversion_value
  FROM g_stats s LEFT JOIN g_name n USING (campaign_id)
),
-- 카카오모먼트: 캠페인 개념이 없어 '소재형식(creative_format)'을 campaign 자리에 매핑.
-- 물리 2테이블(DA=noment: 노출/클릭/비용, 메시지=cost만) 합쳐 통일. 전환은 미수집(NULL).
kakao AS (
  SELECT report_date,
    CASE ad_account_id WHEN '501057' THEN 'cloop' WHEN '800005' THEN 'sprint' ELSE 'unknown' END AS mall,
    'kakao' AS media,
    creative_format AS campaign_id,
    ANY_VALUE(ad_type) AS campaign_name,
    SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(cost) AS cost,
    CAST(NULL AS FLOAT64) AS conversions, CAST(NULL AS FLOAT64) AS conversion_value
  FROM (
    SELECT date AS report_date, ad_account_id, creative_format, ad_type,
           impressions, clicks, cost
    FROM `rf-ads-db-500505.kakao_moment.rf_kakao_moment`
    WHERE date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
    UNION ALL
    SELECT date AS report_date, ad_account_id, creative_format, ad_type,
           0 AS impressions, 0 AS clicks, cost
    FROM `rf-ads-db-500505.kakao_moment.rf_kakao_message`
    WHERE date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  )
  GROUP BY report_date, mall, campaign_id
)
SELECT * FROM meta
UNION ALL SELECT * FROM naver
UNION ALL SELECT * FROM google
UNION ALL SELECT * FROM kakao

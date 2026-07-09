-- 메타 캠페인 × 몰 × 하루 요약 뷰 (마케터용).
-- 평탄화 뷰(rf_meta_ads_flat)를 캠페인 단위로 합산 → 매출 귀속(어트리뷰션) + 효율 지표.
CREATE OR REPLACE VIEW `rf-ads-db-500505.meta_ads.rf_meta_campaign_daily` AS
SELECT
  report_date, mall,
  account_id, campaign_id,
  ANY_VALUE(campaign_name) AS campaign_name,

  SUM(spend)        AS spend,
  SUM(impressions)  AS impressions,
  SUM(clicks)       AS clicks,
  SUM(link_clicks)  AS link_clicks,
  SAFE_DIVIDE(SUM(clicks), SUM(impressions))     AS ctr,
  SAFE_DIVIDE(SUM(spend),  SUM(clicks))          AS cpc,

  -- 핵심 전환 건수
  SUM(purchase_cnt)          AS purchase_cnt,
  SUM(add_to_cart_cnt)       AS add_to_cart_cnt,
  SUM(view_content_cnt)      AS view_content_cnt,
  SUM(register_cnt)          AS register_cnt,
  SUM(lead_cnt)              AS lead_cnt,
  SUM(initiate_checkout_cnt) AS initiate_checkout_cnt,

  -- 매출 귀속액 + 효율
  SUM(purchase_value)                                  AS purchase_value,
  SAFE_DIVIDE(SUM(purchase_value), SUM(spend))         AS roas,
  SAFE_DIVIDE(SUM(spend), SUM(purchase_cnt))           AS cost_per_purchase
FROM `rf-ads-db-500505.meta_ads.rf_meta_ads_flat`
GROUP BY report_date, mall, account_id, campaign_id;

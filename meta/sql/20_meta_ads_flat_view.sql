-- 메타 광고 "평탄화" 뷰 (광고 1건 × 하루 단위).
-- rf_meta_ads 의 actions/action_values JSON 을 풀어 핵심 전환(구매/장바구니/조회/가입 등)을 컬럼으로 제공.
-- 지표 기준: 메타 기본 어트리뷰션(클릭 7일 + 조회 1일). 값(_value)은 원화 매출 귀속액.
CREATE OR REPLACE VIEW `rf-ads-db-500505.meta_ads.rf_meta_ads_flat` AS
SELECT
  report_date,
  -- 몰 구분: 계정ID 기준(통합마트 규칙과 동일)
  CASE
    WHEN account_id IN ('1462607070849777','793134085895227','3589083851393515') THEN 'cloop'
    WHEN account_id = '3342733785912061' THEN 'sprint'
    ELSE 'unknown'
  END AS mall,
  account_id, account_name,
  campaign_id, campaign_name,
  adset_id, adset_name,
  ad_id, ad_name,
  objective, optimization_goal,

  -- 비용/노출/클릭
  spend, impressions, clicks, reach, frequency,
  ctr, cpc, cpm,
  inline_link_clicks AS link_clicks,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['landing_page_view','omni_landing_page_view']) AS landing_page_views,

  -- 핵심 전환 건수 (actions)
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase'])                              AS purchase_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_add_to_cart','add_to_cart','omni_add_to_cart'])                     AS add_to_cart_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_view_content','view_content','omni_view_content'])                  AS view_content_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_complete_registration','complete_registration','omni_complete_registration']) AS register_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_lead','lead','onsite_web_lead'])                                    AS lead_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_initiate_checkout','initiate_checkout','omni_initiated_checkout'])  AS initiate_checkout_cnt,

  -- 핵심 전환 금액 (action_values, 원화 매출 귀속액)
  `rf-ads-db-500505.meta_ads.action_val`(action_values, ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase'])          AS purchase_value,
  `rf-ads-db-500505.meta_ads.action_val`(action_values, ['offsite_conversion.fb_pixel_add_to_cart','add_to_cart','omni_add_to_cart']) AS add_to_cart_value,
  `rf-ads-db-500505.meta_ads.action_val`(action_values, ['offsite_conversion.fb_pixel_view_content','view_content','omni_view_content']) AS view_content_value,

  -- 효율 지표
  SAFE_DIVIDE(`rf-ads-db-500505.meta_ads.action_val`(action_values, ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase']), spend) AS roas,
  SAFE_DIVIDE(spend, `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase']))        AS cost_per_purchase
FROM `rf-ads-db-500505.meta_ads.rf_meta_ads`;

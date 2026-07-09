-- 메타 광고 "평탄화" 뷰 (광고 1건 × 하루 단위).
-- rf_meta_ads 의 actions/action_values JSON 을 풀어 핵심 전환(구매/장바구니/조회/가입 등)을 컬럼으로 제공.
-- 구매는 어트리뷰션 기준 3가지를 함께 제공(원본 수집이 윈도우 포함일 때 채워짐):
--   · 기본(purchase_*)      = 클릭7일 + 조회1일  (메타 광고관리자 기본과 동일)
--   · _incl_ev              = 기본 + 참여1일(1d_ev)
--   · _1dclick              = 클릭1일
-- 그 외 전환(장바구니/조회/가입/잠재고객/결제시작)은 메타 기본 어트리뷰션 기준.
CREATE OR REPLACE VIEW `rf-ads-db-500505.meta_ads.rf_meta_ads_flat` AS
WITH base AS (
  SELECT *,
    `rf-ads-db-500505.meta_ads.action_obj`(actions,       ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase']) AS _pc_obj,
    `rf-ads-db-500505.meta_ads.action_obj`(action_values, ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase']) AS _pv_obj
  FROM `rf-ads-db-500505.meta_ads.rf_meta_ads`
)
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

  -- 구매(기본 = 클릭7일+조회1일)
  SAFE_CAST(JSON_VALUE(_pc_obj, '$.value') AS FLOAT64) AS purchase_cnt,
  SAFE_CAST(JSON_VALUE(_pv_obj, '$.value') AS FLOAT64) AS purchase_value,
  -- 구매(+참여1일)
  SAFE_CAST(JSON_VALUE(_pc_obj, '$.value') AS FLOAT64) + IFNULL(SAFE_CAST(JSON_VALUE(_pc_obj, '$."1d_ev"') AS FLOAT64), 0) AS purchase_cnt_incl_ev,
  SAFE_CAST(JSON_VALUE(_pv_obj, '$.value') AS FLOAT64) + IFNULL(SAFE_CAST(JSON_VALUE(_pv_obj, '$."1d_ev"') AS FLOAT64), 0) AS purchase_value_incl_ev,
  -- 구매(클릭1일)
  SAFE_CAST(JSON_VALUE(_pc_obj, '$."1d_click"') AS FLOAT64) AS purchase_cnt_1dclick,
  SAFE_CAST(JSON_VALUE(_pv_obj, '$."1d_click"') AS FLOAT64) AS purchase_value_1dclick,

  -- 그 외 핵심 전환 건수 (메타 기본 어트리뷰션)
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_add_to_cart','add_to_cart','omni_add_to_cart'])                     AS add_to_cart_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_view_content','view_content','omni_view_content'])                  AS view_content_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_complete_registration','complete_registration','omni_complete_registration']) AS register_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_lead','lead','onsite_web_lead'])                                    AS lead_cnt,
  `rf-ads-db-500505.meta_ads.action_val`(actions, ['offsite_conversion.fb_pixel_initiate_checkout','initiate_checkout','omni_initiated_checkout'])  AS initiate_checkout_cnt,
  -- 그 외 전환 금액
  `rf-ads-db-500505.meta_ads.action_val`(action_values, ['offsite_conversion.fb_pixel_add_to_cart','add_to_cart','omni_add_to_cart']) AS add_to_cart_value,
  `rf-ads-db-500505.meta_ads.action_val`(action_values, ['offsite_conversion.fb_pixel_view_content','view_content','omni_view_content']) AS view_content_value,

  -- 효율(기본 기준)
  SAFE_DIVIDE(SAFE_CAST(JSON_VALUE(_pv_obj, '$.value') AS FLOAT64), spend) AS roas,
  SAFE_DIVIDE(spend, SAFE_CAST(JSON_VALUE(_pc_obj, '$.value') AS FLOAT64)) AS cost_per_purchase
FROM base;

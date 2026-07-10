-- mart.mart_brand_kpi — 몰×일 통합 KPI (광고비 ↔ 실매출 ↔ 웹세션 결합). 대표/경영·마케팅 총괄용.
-- 결합: 광고비(mart_media_daily) + 카페24 실매출(mart_sales_daily) + GA4 세션(rf_ga4).
--   roas_real = 카페24 실결제매출 / 광고비 (매체 리포트가 아닌 '실매출' 기준)
--   cac_per_order = 광고비 / 주문수
-- ⚠️ 광고비는 전 매체 합(자사몰+스마트스토어). 채널 분리가 필요하면 mart_media_daily의 sales_channel 사용.
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_brand_kpi`
PARTITION BY report_date
CLUSTER BY mall AS
WITH spend AS (
  SELECT report_date, mall,
         SUM(spend) AS ad_spend,
         SUM(media_revenue) AS media_reported_revenue
  FROM `rf-ads-db-500505.mart.mart_media_daily`
  GROUP BY report_date, mall
),
sales AS (
  SELECT report_date, mall, revenue AS cafe24_revenue, orders
  FROM `rf-ads-db-500505.mart.mart_sales_daily`
),
ga AS (
  SELECT date AS report_date, brand AS mall,
         SUM(sessions) AS sessions, SUM(total_users) AS users
  FROM `rf-ads-db-500505.rf_ga4.rf_ga4`
  WHERE date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY report_date, mall
)
SELECT
  report_date,
  mall,
  spend.ad_spend,
  spend.media_reported_revenue,
  sales.cafe24_revenue,
  sales.orders,
  ga.sessions,
  ga.users,
  SAFE_DIVIDE(sales.cafe24_revenue, spend.ad_spend)     AS roas_real,     -- 실매출 기준 ROAS
  SAFE_DIVIDE(spend.ad_spend, NULLIF(sales.orders, 0))  AS cac_per_order  -- 주문당 광고비
FROM spend
FULL JOIN sales USING (report_date, mall)
FULL JOIN ga    USING (report_date, mall);

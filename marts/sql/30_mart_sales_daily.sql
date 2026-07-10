-- mart.mart_sales_daily — 일별×몰 매출/주문/객단가/구매자 (결제완료 기준)
-- 출처: cafe24.rf_cafe24_orders (주문 단위). order_status는 전부 NULL이라
--       결제완료 판별은 raw_json $.paid='T' AND $.canceled='F' 사용.
-- 소용량 집계(1년치 수백 행) → Claude 자연어 조회용 마트.
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_sales_daily`
PARTITION BY report_date
CLUSTER BY mall AS
SELECT
  report_date,
  mall,
  COUNT(DISTINCT order_id)                                        AS orders,
  SUM(payment_amount)                                             AS revenue,      -- 실결제액
  SAFE_DIVIDE(SUM(payment_amount), COUNT(DISTINCT order_id))      AS aov,          -- 객단가
  COUNT(DISTINCT member_id)                                       AS member_buyers -- 회원 구매자(게스트 member_id NULL 제외)
FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`
WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 3 YEAR)
  AND JSON_VALUE(raw_json, '$.paid') = 'T'
  AND JSON_VALUE(raw_json, '$.canceled') = 'F'
GROUP BY report_date, mall;

-- mart.mart_sales_daily — 일별×몰 매출 KPI (대시보드 정합)
-- 출처: cafe24.rf_cafe24_orders_current (과거 본표 + 오늘 _d0 → 당일 포함).
-- 결제완료 판별: raw_json $.paid='T' AND $.canceled='F'.
--
-- ★ net_sales(순매출, VAT제외)는 주문의 **actual_order_amount 중첩 재무필드**로 재구성한다.
--    net_charge = 상품가(order_price_amount) + 배송비(shipping_fee)
--               − 적립금(points_spent) − 예치금(credits_spent)
--               − 할인(coupon_discount_price+membership+set_product+app+market_other+shipping_fee_discount+coupon_shipping)
--    net_sales = SUM(net_charge)/1.1.
--    * actual_order_amount는 환불·취소가 반영된 '현재 실주문금액'이라 별도 취소필터 불필요.
--    * 결제수단 무관(상품가 기반)이라 네이버페이 주문도 포함 → payment_amount 방식의 네이버 누락 없음.
--    * 대시보드 순매출(EGNIS 재무 브레이크다운)과 검증: 클룹/스프린트 5·6월 모두 ±0.23% 이내(사실상 일치).
--    * revenue(VAT포함 결제완료 실결제) / orders(결제완료 distinct)는 기존 유지.
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_sales_daily`
PARTITION BY report_date
CLUSTER BY mall AS
WITH o AS (
  SELECT report_date, mall, order_id, member_id, payment_amount,
    (JSON_VALUE(raw_json, '$.paid') = 'T' AND JSON_VALUE(raw_json, '$.canceled') = 'F') AS paid,
    ( SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.order_price_amount') AS FLOAT64)
    + IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.shipping_fee') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.points_spent_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.credits_spent_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.coupon_discount_price') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.membership_discount_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.set_product_discount_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.app_discount_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.market_other_discount_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.shipping_fee_discount_amount') AS FLOAT64), 0)
    - IFNULL(SAFE_CAST(JSON_VALUE(raw_json, '$.actual_order_amount.coupon_shipping_fee_amount') AS FLOAT64), 0)
    ) AS net_charge
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders_current`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 3 YEAR)
)
SELECT
  report_date,
  mall,
  COUNT(DISTINCT IF(paid, order_id, NULL))              AS orders,        -- 결제완료 주문수
  SUM(IF(paid, payment_amount, 0))                      AS revenue,       -- 실결제액(VAT포함, 기존호환)
  ROUND(SUM(net_charge) / 1.1, 2)                       AS net_sales,     -- 순매출(VAT제외, 대시보드 정합)
  SAFE_DIVIDE(SUM(IF(paid, payment_amount, 0)),
              COUNT(DISTINCT IF(paid, order_id, NULL)))  AS aov,
  COUNT(DISTINCT IF(paid, member_id, NULL))             AS member_buyers
FROM o
GROUP BY report_date, mall;

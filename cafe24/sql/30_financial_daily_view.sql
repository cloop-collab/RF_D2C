-- 통합리포트 매출 breakdown BQ 직결용 뷰 (대시보드 EGNIS get_cafe24_daily_financial_breakdown 대체).
-- 소스 = rf_cafe24_orders 주문객체(paid='T'), 일·몰별 집계.
-- mall: cloop = shop_no 1, sprint = shop_no 4 (부속샵 shop_no 2 제외 = EGNIS 스코프와 동일).
-- 필드매핑 검증(2026-07-22, EGNIS 3일 대조): 할인·배송비·적립·네이버 = 정확 일치,
--   상품구매/주문수/품목수 = 스냅샷 시점차 <0.7%(양 시스템 독립 스냅샷 특성상 불가피).
-- 금액 하위필드는 initial_order_amount 기준. 환불은 주문객체에 금액필드가 없어 '취소주문 초기−실 차액(결제일 기준)'으로 파생
--   (EGNIS refund는 환불완료일+네이버근사 기준이라 bit일치 불가 — 대시보드도 네이버환불 참고용 명시).
CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_financial_daily` AS
WITH o AS (
  SELECT report_date, mall, shop_no, order_id, raw_json AS j,
         JSON_VALUE(raw_json, '$.canceled') AS canceled
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`
  WHERE shop_no IN (1, 4)
    AND JSON_VALUE(raw_json, '$.paid') = 'T'
),
it AS (
  SELECT report_date, shop_no, order_id, COUNT(*) AS n
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items`
  WHERE shop_no IN (1, 4)
  GROUP BY report_date, shop_no, order_id
)
SELECT
  o.report_date,
  o.mall,
  COUNT(*)                                                                                   AS order_count,
  SUM(IFNULL(it.n, 0))                                                                        AS item_count,
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.order_price_amount')       AS FLOAT64))) AS product_order_amount,
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.shipping_fee_discount_amount') AS FLOAT64))) AS order_sale_price,   -- 할인
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.shipping_fee')             AS FLOAT64))) AS shipping_fee,
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.coupon_discount_price')    AS FLOAT64))) AS coupon_discount_amount,
  ROUND(SUM(
      SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.order_price_amount')          AS FLOAT64)
    - IFNULL(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.shipping_fee_discount_amount') AS FLOAT64), 0)
    + SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.shipping_fee')                AS FLOAT64)
    - SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.coupon_discount_price')       AS FLOAT64)
  ))                                                                                         AS actual_payment_amount,  -- 결제합계(=상품−할인+배송−쿠폰)
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.points_spent_amount')      AS FLOAT64))) AS used_points,
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.credits_spent_amount')     AS FLOAT64))) AS used_credits,
  ROUND(SUM(SAFE_CAST(JSON_VALUE(j, '$.naver_point')                                   AS FLOAT64))) AS used_naver_total,  -- 네이버포인트/캐시
  ROUND(SUM(CASE WHEN canceled = 'T' THEN
      ( SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.order_price_amount') AS FLOAT64)
        - IFNULL(SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.shipping_fee_discount_amount') AS FLOAT64), 0)
        + SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.shipping_fee') AS FLOAT64)
        - SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.coupon_discount_price') AS FLOAT64) )
    - ( SAFE_CAST(JSON_VALUE(j, '$.actual_order_amount.order_price_amount') AS FLOAT64)
        - IFNULL(SAFE_CAST(JSON_VALUE(j, '$.actual_order_amount.shipping_fee_discount_amount') AS FLOAT64), 0)
        + SAFE_CAST(JSON_VALUE(j, '$.actual_order_amount.shipping_fee') AS FLOAT64)
        - SAFE_CAST(JSON_VALUE(j, '$.actual_order_amount.coupon_discount_price') AS FLOAT64) )
    ELSE 0 END))                                                                             AS refund_amount,   -- 환불(결제일 기준 파생)
  ROUND(SUM(CASE WHEN canceled = 'T' THEN
      SAFE_CAST(JSON_VALUE(j, '$.initial_order_amount.points_spent_amount') AS FLOAT64)
    - SAFE_CAST(JSON_VALUE(j, '$.actual_order_amount.points_spent_amount')  AS FLOAT64)
    ELSE 0 END))                                                                             AS refund_points
FROM o
LEFT JOIN it
  ON it.order_id = o.order_id AND it.report_date = o.report_date AND it.shop_no = o.shop_no
GROUP BY o.report_date, o.mall;

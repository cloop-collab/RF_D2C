-- mart.mart_product_sales — 일별×몰×상품 판매수량/금액 (결제완료 주문의 상품라인)
-- 출처: cafe24.rf_cafe24_order_items(상품라인) ⋈ rf_cafe24_orders(결제완료 필터)
-- 금액=product_price*quantity(정가 기준, 주문단위 쿠폰/할인 미반영 → 상품 인기·수량 분석용).
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_product_sales`
PARTITION BY report_date
CLUSTER BY mall, product_no AS
WITH paid AS (
  SELECT DISTINCT order_id
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
    AND JSON_VALUE(raw_json, '$.paid') = 'T'
    AND JSON_VALUE(raw_json, '$.canceled') = 'F'
)
SELECT
  i.report_date,
  i.mall,
  i.product_no,
  ANY_VALUE(i.product_name)              AS product_name,
  SUM(i.quantity)                        AS qty,
  SUM(i.product_price * i.quantity)      AS sales_amount,
  COUNT(DISTINCT i.order_id)             AS order_count
FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items` i
JOIN paid p USING (order_id)
WHERE i.report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
GROUP BY i.report_date, i.mall, i.product_no;

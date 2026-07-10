-- mart.mart_product_sales — 일별×몰×상품 판매 (취소 반영·VAT 제외 net)
-- 출처: cafe24.rf_cafe24_order_items_current (과거 본표 + 오늘 _d0 → 당일 포함).
-- 취소 반영: 라인 status_code가 'C%'(취소류)면 매출·수량 0 처리.
-- 금액: (product_price + option_price) * quantity. gross=VAT포함, net=÷1.1.
--   net_sales = 대시보드 상품매출 정합(2026-06 클룹 실측 6,608,504,218 정확 재현).
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_product_sales`
PARTITION BY report_date
CLUSTER BY mall, product_no AS
WITH i AS (
  SELECT report_date, mall, product_no, product_name, order_id, quantity, product_price,
    (JSON_VALUE(raw_json, '$.status_code') LIKE 'C%') AS canceled,
    IFNULL(CAST(JSON_VALUE(raw_json, '$.option_price') AS FLOAT64), 0) AS option_price
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items_current`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
)
SELECT
  report_date,
  mall,
  product_no,
  ANY_VALUE(product_name)                                              AS product_name,
  SUM(IF(canceled, 0, quantity))                                       AS qty,
  SUM(IF(canceled, 0, quantity * (product_price + option_price)))      AS gross_sales,  -- VAT 포함, 취소제외
  ROUND(SUM(IF(canceled, 0, quantity * (product_price + option_price))) / 1.1, 2) AS net_sales,  -- VAT 제외
  COUNT(DISTINCT IF(canceled, NULL, order_id))                         AS order_count
FROM i
GROUP BY report_date, mall, product_no;

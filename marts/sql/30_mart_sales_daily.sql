-- mart.mart_sales_daily — 일별×몰 매출 KPI (대시보드 정합)
-- 출처: cafe24 주문(rf_cafe24_orders_current) + 주문상품(rf_cafe24_order_items_current). 당일(_d0) 포함.
-- ⚠ 순매출 net_sales는 **주문상품(order_items) 라인 합계 ÷1.1** 로 계산한다.
--    이유: 주문의 payment_amount는 **네이버페이(별도 tender) 결제분을 제외**해서 순매출이 과소집계됨
--    (2026-06 클룹은 네고왕일 네이버페이≈0이라 우연히 근접했을 뿐). order_items는 결제수단 무관이라
--    네이버페이 주문 상품도 포함 → 대시보드 순매출과 ±0.2% 일치(클룹5·6월·스프린트6월 검증).
--    * revenue      : (기존 호환) 결제완료 실결제액(payment_amount 합), VAT 포함.
--    * net_sales    : 순매출 VAT 제외 = Σ(취소제외 상품라인 (price+option)*qty) / 1.1. 대시보드 헤드라인 정합.
--    * orders       : 결제완료 주문수 distinct (헤드라인 통일 정의).
--    ※ 소수점까지 정확(±0%) 필요 시 EGNIS 재무 브레이크다운(actualPaymentAmount 등) 신규 수집 → 후속.
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_sales_daily`
PARTITION BY report_date
CLUSTER BY mall AS
WITH o AS (
  SELECT report_date, mall,
    COUNT(DISTINCT IF(paid, order_id, NULL))    AS orders,
    SUM(IF(paid, payment_amount, 0))            AS revenue,
    COUNT(DISTINCT IF(paid, member_id, NULL))   AS member_buyers
  FROM (
    SELECT report_date, mall, order_id, member_id, payment_amount,
      (JSON_VALUE(raw_json, '$.paid') = 'T' AND JSON_VALUE(raw_json, '$.canceled') = 'F') AS paid
    FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders_current`
    WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 3 YEAR)
  )
  GROUP BY report_date, mall
),
it AS (
  SELECT report_date, mall,
    SUM(IF(JSON_VALUE(raw_json, '$.status_code') LIKE 'C%', 0,
           quantity * (product_price + IFNULL(CAST(JSON_VALUE(raw_json, '$.option_price') AS FLOAT64), 0)))) AS gross_items
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items_current`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 3 YEAR)
  GROUP BY report_date, mall
)
SELECT
  o.report_date,
  o.mall,
  o.orders,
  o.revenue,
  ROUND(it.gross_items / 1.1, 2)                AS net_sales,     -- 순매출(VAT제외, 결제수단 무관·네이버페이 포함)
  SAFE_DIVIDE(o.revenue, o.orders)              AS aov,
  o.member_buyers
FROM o
LEFT JOIN it USING (report_date, mall);

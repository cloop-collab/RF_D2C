-- mart.mart_sales_daily — 일별×몰 매출 KPI (대시보드 정합)
-- 출처: cafe24.rf_cafe24_orders_current (과거=본표 + 오늘=_d0 → 당일 D0 포함).
-- 결제완료 판별: raw_json $.paid='T' AND $.canceled='F' (order_status는 NULL이라 raw_json 사용).
-- 표준(요청 D): 순매출/상품매출 계열은 VAT 제외(÷1.1) net 기본. gross가 필요하면 별도 컬럼.
--   * revenue      : (기존 호환) 결제완료 실결제액, VAT 포함.
--   * net_sales    : 순매출 VAT 제외 = SUM(payment_amount)/1.1 (전체행, payment_amount가 환불반영 실결제).
--                    대시보드 순매출과 정합(2026-06 클룹 실측 −0.9%). 소수점까지 정확 일치는
--                    EGNIS 재무 브레이크다운 미러링(신규 수집) 필요 → 후속.
--   * orders       : 결제완료 주문수(distinct). 대시보드 헤드라인을 이 정의(BQ 네이티브)로 통일.
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_sales_daily`
PARTITION BY report_date
CLUSTER BY mall AS
WITH o AS (
  SELECT report_date, mall, order_id, member_id, payment_amount,
    (JSON_VALUE(raw_json, '$.paid') = 'T' AND JSON_VALUE(raw_json, '$.canceled') = 'F') AS paid
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders_current`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 3 YEAR)
)
SELECT
  report_date,
  mall,
  COUNT(DISTINCT IF(paid, order_id, NULL))                         AS orders,        -- 결제완료 주문수
  SUM(IF(paid, payment_amount, 0))                                 AS revenue,       -- 실결제액(VAT 포함, 기존호환)
  ROUND(SUM(payment_amount) / 1.1, 2)                              AS net_sales,     -- 순매출(VAT 제외, 환불반영)
  SAFE_DIVIDE(SUM(IF(paid, payment_amount, 0)),
              COUNT(DISTINCT IF(paid, order_id, NULL)))            AS aov,           -- 객단가(VAT 포함 기준)
  COUNT(DISTINCT IF(paid, member_id, NULL))                        AS member_buyers -- 회원 구매자
FROM o
GROUP BY report_date, mall;

-- 카페24 재구매/첫재구매 일별 뷰 (전용 통계 API가 없어 주문 데이터로 계산)
-- 회원별 주문을 시간순으로 매겨(purchase_seq), 그 주문이 일어난 날짜에 집계.
--   first_purchase    : 회원의 1회차(신규) 구매 건수
--   first_repurchase  : 회원의 2회차 = '첫 재구매' 건수
--   repurchase_orders : 2회차 이상(재구매) 건수
--   repurchase_rate   : 재구매 건수 / 회원 주문 건수
-- ※ 비회원(member_id 없음)은 동일인 식별 불가라 제외. 주문 상태(취소 포함) 전체 기준.
CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_repurchase_daily` AS
WITH seq AS (
  SELECT
    mall, shop_no, member_id, report_date, order_id, ordered_at,
    ROW_NUMBER() OVER (
      PARTITION BY mall, member_id
      ORDER BY ordered_at, order_id
    ) AS purchase_seq
  FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`
  WHERE member_id IS NOT NULL AND member_id != ''
)
SELECT
  report_date,
  mall,
  COUNT(*)                               AS member_orders,
  COUNTIF(purchase_seq = 1)              AS first_purchase,
  COUNTIF(purchase_seq = 2)              AS first_repurchase,
  COUNTIF(purchase_seq >= 2)             AS repurchase_orders,
  SAFE_DIVIDE(COUNTIF(purchase_seq >= 2), COUNT(*)) AS repurchase_rate
FROM seq
GROUP BY report_date, mall

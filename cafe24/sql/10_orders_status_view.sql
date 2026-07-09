-- 주문 상태 파생 뷰.
-- 원본 rf_cafe24_orders 의 order_status 컬럼은 API 응답에 없어 전부 NULL → raw_json 의
-- paid(T/F=결제여부)·canceled(F/T/M=취소여부, M=부분취소)로 상태를 복원한다.
-- 순매출 집계 시 status_label='결제완료' 또는 is_paid AND NOT is_canceled 로 필터.
CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_orders_status` AS
SELECT
  report_date, mall, shop_no, order_id, member_id,
  payment_amount, order_amount, currency, ordered_at,
  JSON_VALUE(raw_json, '$.paid')     AS paid_flag,      -- T/F
  JSON_VALUE(raw_json, '$.canceled') AS canceled_flag,  -- F/T/M
  JSON_VALUE(raw_json, '$.paid') = 'T'                     AS is_paid,
  JSON_VALUE(raw_json, '$.canceled') = 'T'                 AS is_canceled,
  JSON_VALUE(raw_json, '$.canceled') = 'M'                 AS is_partial_cancel,
  CASE
    WHEN JSON_VALUE(raw_json, '$.canceled') = 'T' THEN '취소'
    WHEN JSON_VALUE(raw_json, '$.canceled') = 'M' THEN '부분취소'
    WHEN JSON_VALUE(raw_json, '$.paid')     = 'T' THEN '결제완료'
    WHEN JSON_VALUE(raw_json, '$.paid')     = 'F' THEN '미결제'
    ELSE '기타'
  END AS status_label,
  raw_json, ingested_at
FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`;

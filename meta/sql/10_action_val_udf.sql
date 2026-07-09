-- 메타 actions/action_values(JSON 문자열)에서 특정 전환값을 "우선순위"로 하나 뽑는 함수.
--   arr   : actions 또는 action_values 컬럼(JSON 배열 문자열)
--   types : 찾을 action_type 우선순위 목록(앞에 있을수록 우선). 예: ['offsite_conversion.fb_pixel_purchase','purchase','omni_purchase']
-- 동작: 배열에서 우선순위가 가장 높은(=목록 앞쪽) action_type의 value 를 반환. 없으면 NULL.
-- 파이프라인 meta_to_bigquery.py 의 _first_action_value() 와 동일한 규칙(픽셀 우선) → web_purchase_* 컬럼과 일치.
CREATE OR REPLACE FUNCTION `rf-ads-db-500505.meta_ads.action_val`(arr STRING, types ARRAY<STRING>)
RETURNS FLOAT64 AS ((
  SELECT CAST(JSON_VALUE(a, '$.value') AS FLOAT64)
  FROM UNNEST(JSON_EXTRACT_ARRAY(arr)) AS a
  JOIN UNNEST(types) AS t WITH OFFSET AS pri
    ON JSON_VALUE(a, '$.action_type') = t
  ORDER BY pri
  LIMIT 1
));

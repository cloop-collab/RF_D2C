-- 광고 어드민 다운로드 RAW 스테이징 테이블 (과거 이력 보완용)
-- load_ad_manual.py 가 marts/manual_ad_data/*.csv 를 MERGE 적재.
-- ad_unified_src 뷰가 UNION하되 같은 (media,mall,일자)는 API 우선.
CREATE TABLE IF NOT EXISTS `rf-ads-db-500505.mart.ad_manual` (
  report_date DATE,
  mall STRING,
  media STRING,
  campaign_id STRING,
  campaign_name STRING,
  impressions INT64,
  clicks INT64,
  cost FLOAT64,
  conversions FLOAT64,
  conversion_value FLOAT64,
  source STRING,           -- 원본 파일명
  loaded_at TIMESTAMP
) PARTITION BY report_date CLUSTER BY media, mall;

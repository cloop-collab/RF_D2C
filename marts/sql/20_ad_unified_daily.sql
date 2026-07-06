-- 통합 소스 뷰를 물리 테이블로 굽기(파티션/클러스터) → Claude 조회용 저비용 마트
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.ad_unified_daily`
PARTITION BY report_date CLUSTER BY mall, media AS
SELECT * FROM `rf-ads-db-500505.mart.ad_unified_src`

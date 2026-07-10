-- mart.mart_media_daily — 매체×몰×판매채널×일 광고 성과 요약 (광고비·노출·클릭·CTR·전환·ROAS)
-- 출처: mart.ad_unified_daily(4매체 통합, 캠페인 단위)를 매체/몰/채널/일로 롤업.
-- 콘텐츠·퍼포먼스 마케터용: "매체별 노출·CTR·광고비·ROAS" 한 표.
-- ⚠️ 구글 과거 광고비는 백필 진행 중이라 옛 날짜는 채워지는 중(최근은 정상). 매일 재생성되며 자동 보정.
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_media_daily`
PARTITION BY report_date
CLUSTER BY mall, media AS
SELECT
  report_date,
  mall,
  media,
  sales_channel,
  SUM(cost)                                                       AS spend,          -- 광고비(VAT 매체별 상이, 컨텍스트 정본 참고)
  SUM(impressions)                                                AS impressions,
  SUM(clicks)                                                     AS clicks,
  SAFE_DIVIDE(SUM(clicks), NULLIF(SUM(impressions), 0))           AS ctr,            -- 클릭률
  SUM(conversions)                                                AS conversions,
  SUM(conversion_value)                                           AS media_revenue,  -- 매체 리포트 전환매출(귀속 기준=매체 자체)
  SAFE_DIVIDE(SUM(conversion_value), NULLIF(SUM(cost), 0))        AS roas_media      -- 매체 기준 ROAS
FROM `rf-ads-db-500505.mart.ad_unified_daily`
GROUP BY report_date, mall, media, sales_channel;

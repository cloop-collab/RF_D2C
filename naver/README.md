# Naver 검색광고 → BigQuery 적재

네이버 검색광고(SA) 성과를 BigQuery `naver_ads` 데이터셋의 **단일 테이블**에 적재.
캠페인 + 키워드를 한 테이블에 담고 `level`(campaign/keyword)로 구분.

## 테이블
- `rf_naver_sa_ads`     : 매일 06:00 KST, 최근 7일 재적재 (확정)
- `rf_naver_sa_ads_d0`  : 매시간, 오늘 하루 (실시간)

## 컬럼
report_date, media, account, customer_id, level, campaign_id, campaign_name,
adgroup_id, adgroup_name, keyword_id, keyword, lp_type,
impressions, clicks, cost, ctr, cpc, avg_rank, conversions, conv_rate,
cost_per_conv, conversion_value, roas, raw_json, ingested_at
* 캠페인 행은 adgroup/keyword 컬럼이 NULL, level='campaign'
* 키워드 행은 전부 채워지고 level='keyword'

## LP 구분
SA=캠페인명 '스토어/스스'→스마트스토어, DA='asd'→스마트스토어, 그 외 자사몰

## 백필
Daily 워크플로 Run workflow → backfill_days 입력 (예: 365=1년)

## GitHub Secrets
GCP_SA_KEY(기존), NAVER_CLOOP_LICENSE/SECRET/CUSTOMER_ID,
NAVER_SPRINT_LICENSE/SECRET/CUSTOMER_ID

## 참고
- 키워드가 없는 캠페인 유형(브랜드검색/쇼핑검색/플레이스 등)은 campaign 행으로만 존재.
- 전환계열 지표는 계정 전환추적 미설정 시 자동으로 기본 지표만 적재(폴백).
- NAVER_LEVEL=campaign|keyword 로 한쪽만 돌릴 수도 있음(기본 both).

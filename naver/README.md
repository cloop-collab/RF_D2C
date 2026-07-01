# Naver 검색광고 → BigQuery 적재

네이버 검색광고(SA) 성과를 BigQuery `naver_ads` 데이터셋에 적재.
하나의 스크립트(naver_to_bigquery.py)가 NAVER_LEVEL 로 단위 선택.

## 테이블
하나의 Daily/Hourly 워크플로에서 캠페인→키워드를 순서대로 적재.
- Daily(매일 06:00 KST, 7일): `rf_naver_sa_ads`, `rf_naver_sa_kw`
- Hourly(매시간, 당일): `rf_naver_sa_ads_d0`, `rf_naver_sa_kw_d0`

## 지표(최대치)
impressions, clicks, cost, ctr, cpc, avg_rank, conversions, conv_rate,
cost_per_conv, conversion_value, roas + raw_json(원본 전체)

## LP 구분
SA=캠페인명 '스토어/스스'→스마트스토어, DA='asd'→스마트스토어, 그 외 자사몰

## 백필
Daily 워크플로 Run workflow → backfill_days 입력 (예: 365=1년)

## GitHub Secrets
GCP_SA_KEY(기존), NAVER_CLOOP_LICENSE/SECRET/CUSTOMER_ID,
NAVER_SPRINT_LICENSE/SECRET/CUSTOMER_ID

## 참고
- 키워드 단위는 캠페인>광고그룹>키워드를 순회해 수집(호출 많음, 백필 시 시간 소요).
- 키워드가 없는 캠페인 유형(브랜드검색/쇼핑검색/플레이스 등)은 키워드 테이블에 안 잡힘 → 캠페인 테이블에서 확인.
- 전환계열 지표는 계정 전환추적 미설정 시 자동으로 기본 지표만 적재(폴백).

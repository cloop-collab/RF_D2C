# Naver 검색광고 → BigQuery 적재

네이버 검색광고(SA) 성과를 BigQuery `naver_ads` 데이터셋에 적재.

- 일/주 확정: `rf_naver_sa_ads` (매일 06:00 KST, 최근 7일 재적재)
- 시간당 당일: `rf_naver_sa_ads_d0` (매시간, 오늘 하루)
- LP 구분: SA=캠페인명 '스토어/스스'→스마트스토어, DA='asd'→스마트스토어, 그 외 자사몰

## 필요한 GitHub Secrets
GCP_SA_KEY(기존), NAVER_CLOOP_LICENSE, NAVER_CLOOP_SECRET, NAVER_CLOOP_CUSTOMER_ID,
NAVER_SPRINT_LICENSE, NAVER_SPRINT_SECRET, NAVER_SPRINT_CUSTOMER_ID

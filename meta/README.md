# 메타(Facebook/Instagram) 광고 데이터

## 원본 테이블
- `meta_ads.rf_meta_ads` : 광고 1건 × 하루 단위 원본. 전환은 `actions`/`action_values` 안에 JSON으로 묶여 있어 바로 보기 어렵다.

## 평탄화 뷰 (여기서 만든 것) — 전환을 컬럼으로 풀어놓음
`meta/sql/*.sql` 로 정의하고, 매일 적재 후 `meta/refresh_meta_views.py`(meta_daily.yml)가 자동 갱신한다.

### 1) `meta_ads.rf_meta_ads_flat` — 광고 × 하루 (상세)
핵심 전환을 컬럼으로 제공. 주요 컬럼:
- 식별: `report_date`, `mall`(cloop/sprint), `campaign_name`, `adset_name`, `ad_name`
- 비용/노출: `spend`, `impressions`, `clicks`, `ctr`, `cpc`, `link_clicks`, `landing_page_views`
- 전환 건수: `purchase_cnt`(구매), `add_to_cart_cnt`(장바구니), `view_content_cnt`(조회), `register_cnt`(가입), `lead_cnt`(잠재고객), `initiate_checkout_cnt`(결제시작)
- 전환 금액: `purchase_value`(구매 매출), `add_to_cart_value`, `view_content_value`
- 효율: `roas`(구매매출÷광고비), `cost_per_purchase`(구매당 광고비)

### 2) `meta_ads.rf_meta_campaign_daily` — 캠페인 × 몰 × 하루 (요약, 마케터용)
위 상세 뷰를 캠페인 단위로 합산. 캠페인별 광고비·구매수·구매액·ROAS를 바로 조회.

## 참고
- **어트리뷰션 기준**: 메타 기본값(클릭 후 7일 + 조회 후 1일). 원본 API 호출이 기본 윈도우만 받으므로 뷰도 동일 기준.
- **전환 우선순위**: 각 전환은 픽셀 이벤트 우선(`offsite_conversion.fb_pixel_*`) → 표준 이벤트 → omni 순으로 값을 하나 선택(`meta_ads.action_val` 함수). 파이프라인의 `web_purchase_*` 컬럼과 동일한 규칙이라 구매 수치가 일치한다.
- **몰 구분**: 계정ID 기준(cloop = 1462607070849777·793134085895227·3589083851393515, sprint = 3342733785912061). 통합마트 규칙과 동일.

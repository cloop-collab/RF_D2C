# Cafe24 → BigQuery 파이프라인

카페24 멀티쇼핑몰(클룹/스프린트)을 BigQuery 데이터셋 `cafe24`에 적재.
통합 원본 테이블 1벌(`shop_no`/`mall` 컬럼) + 몰별 뷰 자동 생성.

## 테이블

| 종류 | 테이블 | 소스 |
|---|---|---|
| 통계 | `rf_cafe24_sales_daily` / `_d0` | `/sales/times` |
| 통계 | `rf_cafe24_product_sales` / `_d0` | `/products/sales` |
| 통계 | `rf_cafe24_traffic` / `_d0` | `/visitors/view` |
| 통계 | `rf_cafe24_traffic_keyword` / `_d0` | `/visitpaths/keywords` |
| 통계 | `rf_cafe24_members` / `_d0` | `/members/sales` |
| 통계 | `rf_cafe24_cart_action` / `_d0` | `/carts/action` (장바구니 담긴수·담기율) |
| 통계 | `rf_cafe24_product_view` / `_d0` | `/products/view` (상품 조회수) |
| 통계 | `rf_cafe24_visitors_unique` / `_d0` | `/visitors/unique` (순방문자) |
| 통계 | `rf_cafe24_visitors_pageview` / `_d0` | `/visitors/pageview` (페이지뷰) |
| 통계 | `rf_cafe24_visitors_dau` / `_d0` | `/visitors/dailyactive` (DAU) |
| 통계 | `rf_cafe24_sales_paymethod` / `_d0` | `/sales/paymethods` (결제수단별 매출) |
| 통계 | `rf_cafe24_keyword_detail` / `_d0` | `/visitpaths/keyworddetails` (키워드 구매전환) |
| 통계 | `rf_cafe24_referrer_domain` / `_d0` | `/visitpaths/domains` (유입 도메인) |
| 통계 | `rf_cafe24_referrer_ad` / `_d0` | `/visitpaths/ads` (광고매체 유입) |
| 회원 | `rf_cafe24_member_joins` / `_d0` | `/customersprivacy/count` (가입수 일별) |
| 관리 | `rf_cafe24_orders` / `_d0` | `/orders?embed=items` |
| 관리 | `rf_cafe24_order_items` / `_d0` | 위 주문의 items |
| 관리 | `rf_cafe24_products` | `/products` (일 스냅샷) |
| 파생 | `rf_cafe24_repurchase_daily` (뷰) | orders 계산 (첫재구매·재구매·재구매율) |
| 내부 | `oauth_state` | OAuth 토큰 저장(access/refresh 회전) |

몰별 뷰: 각 테이블마다 `<table>_cloop`, `<table>_sprint` 자동 생성.

**공통 스키마(통계)**: `report_date, shop_no, mall, dim1, dim2, val1, val2, raw_json, ingested_at`.
`dim1/dim2`=차원(상품·키워드·결제수단 등), `val1/val2`=대표 수치, 나머지 지표는 `raw_json`에 원본 보존.
날짜 없는 집계형(`per_day=True`: 장바구니·상품조회·결제수단·유입세부)은 **하루씩 호출**해 일자별로 정확히 적재.

### 재구매 뷰 적용 (1회)
```bash
cat cafe24/sql/repurchase_daily_view.sql | bq query --use_legacy_sql=false
```

### 가입수 (2026-07 추가, `mall.read_privacy` 필요)
- `rf_cafe24_member_joins`: `/customersprivacy/count?date_type=join&start_date=D&end_date=D` 를 **일자별로 호출해 개수만** 적재. **개인정보 원본은 저장하지 않음**(count 엔드포인트는 숫자만 반환).
- 몰 매핑: shop_no=1→cloop, shop_no=4→sprint (shop_no=2는 cloop 부속·소량).
- 이 테이블만 스코프 **`mall.read_privacy`(개인정보 Privacy)** 필요 → 앱에 추가 후 **재동의**로 토큰에 반영(`get_customer_scope_token.py` 참고). Redirect URI: `https://github.com/cloop-collab/RF_D2C`.

## GitHub Secrets (필요)

| 이름 | 설명 |
|---|---|
| `CAFE24_MALL_ID` | 대표 몰 아이디(관리 API 서브도메인) |
| `CAFE24_CLIENT_ID` | 개발자센터 앱 Client ID |
| `CAFE24_CLIENT_SECRET` | 앱 Client Secret |
| `CAFE24_REFRESH_TOKEN` | 최초 시드용 refresh token (첫 실행 후 BigQuery가 관리) |
| `GCP_SA_KEY` | 기존 BigQuery 서비스계정 키(공용) |

앱 스코프(읽기전용): `mall.read_order`, `mall.read_product`, `mall.read_customer`, `mall.read_analytics`

## 워크플로

- `cafe24_daily.yml` — 매일 06:00 KST, 최근 7일 재적재. 수동 입력: `backfill_days`(예 365), `orders_full`(1=주문 전체기간), `tables`(대상 제한).
- `cafe24_d0_intraday.yml` — 매시간, 당일(`_d0`) 통계+주문 갱신.

## 주의

- **refresh 토큰 회전**: 카페24는 갱신 시마다 refresh 토큰이 바뀜 → `oauth_state`에 즉시 저장.
  일별/시간당 워크플로가 동시에 갱신하면 충돌 가능하나, access(2h) 유효 중엔 갱신하지 않아 실제 충돌은 드묾.
- **지표 컬럼은 best-effort**: 정확한 필드명은 첫 실행의 `raw_json`을 보고 보강 예정.
  (`dim1/dim2/val1/val2` = 통계 대표값 자리, 실데이터 확인 후 명명)
- 주문 API는 조회 구간 3개월 제한 → 90일 윈도우로 분할 수집.

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
| 관리 | `rf_cafe24_orders` / `_d0` | `/orders?embed=items` |
| 관리 | `rf_cafe24_order_items` / `_d0` | 위 주문의 items |
| 관리 | `rf_cafe24_products` | `/products` (일 스냅샷) |
| 관리 | `rf_cafe24_customers` | `/customers` (일 스냅샷) |
| 내부 | `oauth_state` | OAuth 토큰 저장(access/refresh 회전) |

몰별 뷰: 각 테이블마다 `<table>_cloop`, `<table>_sprint` 자동 생성.

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

# BigQuery 마트/뷰 설계안 — Claude 자연어 분석용

> 목적: 데이터 초보자가 **Claude에 자연어로 질문 → Claude가 BigQuery 조회 → 답변** 하는 워크플로에서,
> 비용 폭주 없이 안전하게 매체·카페24·GA4 데이터를 교차 분석하도록 하는 데이터 계층 설계.
> 프로젝트 `rf-ads-db-500505` · 리전 `asia-northeast3`

---

## 1. 핵심 원칙 (왜 이렇게?)

1. **뷰만으론 비용이 안 줄어든다.** 일반 View는 조회 시 원본을 그대로 스캔함.
   → 실제 비용 절감은 **작게 미리 집계한 물리 테이블(마트)** 을 매일 만들어 두고, Claude가 그것만 조회하게 하는 것.
2. **Claude 연결 계정은 마트(`mart` 데이터셋)만 접근.** 원본(raw)엔 권한 없음 → 초보자가 뭘 물어도 원본 전체 스캔 불가.
3. **마트는 작다.** 일별×몰×매체 집계 = 1년치도 수천 행(수 KB~MB). 질문 100번 해도 비용 미미.
4. **민감정보(주문자·연락처)는 마트에 넣지 않는다.** 회원은 ID/등급 수준 집계만.

계층:
```
raw (원본, 대용량)          meta_ads / naver_ads / rf_ga4 / cafe24 / google_ads
   │  (매일 1회 집계 = 스케줄 쿼리)
   ▼
mart (집계, 소용량) ★Claude가 보는 곳   mart_media_daily / mart_sales_daily / ...
   │  (직무별 노출 제한)
   ▼
authorized views (직무별)   v_mkt_* / v_md_* / v_fin_*
```

---

## 2. 몰/매체 구분 규칙 (마트 빌드시 표준화)

| 원본 | 몰 구분 | 매체값 |
|---|---|---|
| meta_ads | `account_id`(클룹 계정들→cloop / 스프린트 계정→sprint) — ※실데이터 검증됨, adset 접두어 방식은 오답 | `meta` |
| naver_ads | `account` (CLOOP→cloop / SPRINT→sprint) | `naver_sa` |
| rf_ga4 | `brand` (cloop/sprint) | (attribution) |
| cafe24 | `mall` (cloop/sprint) | (sales) |
| google_ads | (토큰 승인 후 추가) | `google` |

---

## 3. 마트 DDL (매일 1회 재생성 = 스케줄 쿼리)

> 모두 `CREATE OR REPLACE TABLE` — 작은 집계라 매일 통째로 다시 만들어도 저렴.
> 스케줄 쿼리(또는 GitHub Actions 1스텝)로 매일 새벽 raw 적재 후 실행.

### 3-1. `mart.mart_media_daily` — 매체·몰·일별 광고비/성과 (마케팅 핵심)

```sql
CREATE SCHEMA IF NOT EXISTS `rf-ads-db-500505.mart`
  OPTIONS(location='asia-northeast3');

CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_media_daily`
PARTITION BY report_date
CLUSTER BY mall, media AS
WITH meta AS (
  SELECT
    report_date,
    CASE WHEN STARTS_WITH(adset_name,'fb_cl') THEN 'cloop'
         WHEN STARTS_WITH(adset_name,'fb_sp') THEN 'sprint' END AS mall,
    'meta' AS media,
    campaign_name,
    SUM(spend)               AS spend,
    SUM(impressions)         AS impressions,
    SUM(clicks)              AS clicks,
    SUM(web_purchase_count)  AS conversions,
    SUM(web_purchase_value)  AS media_revenue
  FROM `rf-ads-db-500505.meta_ads.rf_meta_ads`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY 1,2,3,4
),
naver AS (
  SELECT
    report_date,
    LOWER(account) AS mall,          -- CLOOP/SPRINT → cloop/sprint
    'naver_sa' AS media,
    campaign_name,
    SUM(cost)             AS spend,
    SUM(impressions)      AS impressions,
    SUM(clicks)           AS clicks,
    SUM(conversions)      AS conversions,
    SUM(conversion_value) AS media_revenue
  FROM `rf-ads-db-500505.naver_ads.rf_naver_sa_ads`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
    AND level = 'campaign'           -- 캠페인 단위만(키워드 중복 방지)
  GROUP BY 1,2,3,4
)
SELECT * FROM meta WHERE mall IS NOT NULL
UNION ALL
SELECT * FROM naver;
-- google_ads: 개발자 토큰 승인 후 동일 패턴으로 UNION 추가
```

### 3-2. `mart.mart_sales_daily` — 카페24 일별 매출/주문 (경영·MD·마케팅 공통)

> 통계 API(placeholder 컬럼) 대신 **주문 원본(typed)** 에서 집계 → 신뢰도 높음.

```sql
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_sales_daily`
PARTITION BY report_date
CLUSTER BY mall AS
SELECT
  report_date,
  mall,
  COUNT(DISTINCT order_id)                       AS orders,
  SUM(payment_amount)                            AS revenue,        -- 실결제액
  SAFE_DIVIDE(SUM(payment_amount),
              COUNT(DISTINCT order_id))          AS aov,            -- 객단가
  COUNT(DISTINCT member_id)                      AS buyers
FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`
WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 3 YEAR)
GROUP BY 1,2;
```

### 3-3. `mart.mart_product_sales` — 상품별 판매 (MD)

```sql
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_product_sales`
PARTITION BY report_date
CLUSTER BY mall, product_no AS
SELECT
  report_date,
  mall,
  product_no,
  ANY_VALUE(product_name)      AS product_name,
  SUM(quantity)                AS qty,
  SUM(product_price*quantity)  AS sales_amount,
  COUNT(DISTINCT order_id)     AS order_count
FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items`
WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
GROUP BY 1,2,3;
```

### 3-4. `mart.mart_brand_daily_kpi` — 몰·일별 통합 KPI (대표/경영, 광고↔매출 결합)

```sql
CREATE OR REPLACE TABLE `rf-ads-db-500505.mart.mart_brand_daily_kpi`
PARTITION BY report_date
CLUSTER BY mall AS
WITH spend AS (
  SELECT report_date, mall, SUM(spend) AS ad_spend,
         SUM(media_revenue) AS media_reported_revenue
  FROM `rf-ads-db-500505.mart.mart_media_daily` GROUP BY 1,2
),
sales AS (
  SELECT report_date, mall, revenue AS cafe24_revenue, orders
  FROM `rf-ads-db-500505.mart.mart_sales_daily`
),
ga AS (
  SELECT date AS report_date, brand AS mall,
         SUM(sessions) AS sessions, SUM(total_users) AS users
  FROM `rf-ads-db-500505.rf_ga4.rf_ga4`
  WHERE date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY 1,2
)
SELECT
  COALESCE(sp.report_date, sa.report_date, ga.report_date) AS report_date,
  COALESCE(sp.mall, sa.mall, ga.mall)                      AS mall,
  sp.ad_spend,
  sa.cafe24_revenue,
  sa.orders,
  ga.sessions,
  ga.users,
  SAFE_DIVIDE(sa.cafe24_revenue, sp.ad_spend)  AS roas_실매출기준,
  SAFE_DIVIDE(sp.ad_spend, sa.orders)          AS cac_주문당광고비
FROM spend sp
FULL JOIN sales sa USING (report_date, mall)
FULL JOIN ga   ga USING (report_date, mall);
```

---

## 4. 직무별 노출 (authorized views — 마트 위에서 제한)

마트는 작으니 여기선 뷰 사용 OK. 각 직무 서비스계정/그룹엔 **해당 뷰만** `dataViewer` 부여.

| 직무 | 접근 뷰 | 내용 |
|---|---|---|
| 마케팅/퍼포먼스 | `v_mkt_media_daily`, `v_mkt_brand_kpi` | 매체 성과 + 통합 KPI |
| MD/상품 | `v_md_product_sales`, `v_md_sales_daily` | 상품·매출 |
| 경영/재무 | `v_fin_brand_kpi`, `v_fin_sales_daily` | 통합 KPI·매출(광고비 상세는 요약만) |
| 대표/경영진 | `v_exec_kpi` | 몰·주간 요약 KPI만 |

```sql
-- 예시: 마케팅용 뷰
CREATE OR REPLACE VIEW `rf-ads-db-500505.mart.v_mkt_brand_kpi` AS
SELECT report_date, mall, ad_spend, cafe24_revenue, orders, sessions, roas_실매출기준
FROM `rf-ads-db-500505.mart.mart_brand_daily_kpi`;
```

---

## 5. 비용 안전장치 (마트와 함께 필수 설정)

1. **Claude 연결 전용 서비스계정** = `mart` 데이터셋에만 `roles/bigquery.dataViewer` + `roles/bigquery.jobUser`. raw 데이터셋 접근 금지.
2. **쿼리당 상한**: 연결 기본값 `maximum_bytes_billed = 1GB` (넘으면 과금 없이 실패).
3. **사용자/계정당 1일 스캔 할당량**: 예 50GB/day (커스텀 quota).
4. 원본 대형 테이블(`rf_cafe24_orders` 등)에 `require_partition_filter = TRUE` → 날짜필터 없는 전체스캔 차단.
5. Claude 시스템 규칙: "① 항상 마트 우선 ② 날짜범위 명시 ③ 원본 직접 조회는 승인 후".

---

## 6. 갱신 방식

- 마트 재생성 = **BigQuery 스케줄 쿼리** 매일 새벽(원본 daily 적재 이후, 예 07:00 KST) 1회.
- 또는 GitHub Actions에 `bq query` 1스텝 추가(기존 워크플로 뒤에).

---

## 7. 다음 단계 / 확정 필요

- [ ] 카페24 통계(sales_daily 등)의 `dim/val` 컬럼을 raw_json 기준으로 확정(현재 placeholder). 단, 매출 마트는 orders 기반이라 영향 없음.
- [ ] meta 컬럼명 최종 확인(`account_name`, `ad_name` 등) — 위 DDL은 확인된 컬럼만 사용.
- [ ] google_ads: 개발자 토큰 승인 후 `mart_media_daily`에 UNION 추가.
- [ ] 직무별 서비스계정·IAM 실제 부여 + quota 설정.
- [ ] 직무별 사용 가이드라인 문서(예시 질문/금지 패턴).

---

## 8. 광고 데이터 테이블 세분화 방향

### 8-1. 현재 상태 (매체별 raw)
| 매체 | 테이블 | 단위(grain) | 몰 구분 | 광고비 컬럼 | 전환 컬럼 |
|---|---|---|---|---|---|
| 메타 | meta_ads.rf_meta_ads | 광고(ad) 단위 + nested JSON | `adset_name` fb_cl/fb_sp | `spend` | `web_purchase_value/count` + JSON(actions 등) |
| 네이버 | naver_ads.rf_naver_sa_ads | `level`=campaign/keyword 통합 | `account`(CLOOP/SPRINT) | `cost` | `conversions`/`conversion_value`/`roas` |
| 구글 | google_ads.campaign_daily / keyword_daily | 캠페인·키워드 분리 | (규칙 확인 필요) | `cost` | `conversions`/`conversions_value` |
| GA4 | rf_ga4.rf_ga4 | 일·UTM 단위 | `brand` | — | `ecommerce_purchases`/`purchase_revenue` |

**문제**: 컬럼명·구조·몰구분 방식이 매체마다 달라 (spend vs cost, web_purchase_value vs conversion_value…) → 초보자 자연어 질문·매체 비교가 어려움. 메타는 핵심 전환이 JSON 안에 묻혀 있음.

### 8-2. 제안: 3단계 세분화

**[1단계] 매체 공통 표준 테이블 `ad_unified_daily`** (가장 우선)
모든 매체를 **동일 컬럼**으로 통일 → "지난주 매체별 광고비·ROAS 비교" 같은 질문이 한 테이블로 해결.
```
report_date, mall, media, level,
campaign_id, campaign_name, sub_id, sub_name,   -- sub = 키워드/광고소재/애드셋
impressions, clicks, cost, conversions, conversion_value,
roas(=conversion_value/cost)
```
매체별 매핑: 메타(cost=spend, conv=web_purchase_count, mall=adset접두어) · 네이버(cost=cost, mall=account, level=level) · 구글(cost=cost, mall=규칙확인).

**[2단계] 목적별 레벨 테이블**
- `ad_campaign_daily` — 캠페인 요약(전 매체) → 일상 성과 모니터링
- `ad_creative_daily` — 메타 소재/광고 단위 → 크리에이티브 분석
- `ad_keyword_daily` — 네이버+구글 키워드 → 검색 키워드 분석

**[3단계] 메타 전환 평탄화 뷰**
nested JSON(`actions`/`action_values`/`purchase_roas`)에서 핵심 전환(구매·장바구니·가입·조회)과 어트리뷰션(7일클릭 등)을 **플랫 컬럼**으로 추출 → NL 질문 대응.

**[교차] 어트리뷰션 결합 마트** (선택)
광고 `campaign_name` ↔ GA4 `session_campaign_name` ↔ 카페24 매출 연결.
※ 전제: UTM/캠페인 네이밍 규칙 표준화 (현재 카페24 주문엔 UTM 없음 → GA4가 연결고리).

### 8-3. 세분화 시 확정 필요
- 매체별 **몰 구분 규칙 통일** (메타=adset접두어, 네이버=account, 구글=?, GA4=brand).
- 메타 raw **grain 확인**(ad 단위면 캠페인 집계 시 중복 없음 확인).
- 구글 몰 구분 방법(캠페인명 규칙 등) — 토큰 승인 후 실데이터로 확정.

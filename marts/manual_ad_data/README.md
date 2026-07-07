# 광고 어드민 RAW 수동 적재

API로 안 잡히는 **과거 광고 이력**을 각 매체 어드민에서 내려받아 여기에 CSV로 올리면,
통합 마트(`mart.ad_unified_daily`)에 자동 병합됩니다. **중복 걱정 없음** — 같은 (매체·몰·일자)는
API 데이터가 우선하고, API가 없는 과거 구간만 이 RAW로 채웁니다.

## 왜 필요한가
현재 API 광고비 이력: kakao 1년 / naver 3개월 / meta 2개월 / google 1개월.
그 이전 프로모션의 마케팅비·ROAS를 채우려면 어드민 RAW가 필요합니다.

## 사용법
1. `_TEMPLATE.csv` 를 복사해 새 파일로 만드세요(예: `meta_2025.csv`). **`_`로 시작하는 파일은 적재 제외**(예시용).
2. 아래 표준 컬럼으로 채웁니다(어드민 export를 이 형식으로 정리):

   | 컬럼 | 설명 |
   |---|---|
   | `report_date` | YYYY-MM-DD |
   | `mall` | cloop / sprint |
   | `media` | meta / naver / google / kakao / tiktok … |
   | `campaign_id` | (선택) 없으면 비움 |
   | `campaign_name` | 캠페인명(중복 병합 키) |
   | `impressions` | 노출수 |
   | `clicks` | 클릭수 |
   | `cost` | 비용(원) — 콤마·₩ 있어도 자동 제거 |
   | `conversions` | 전환수 (없으면 비움) |
   | `conversion_value` | 전환매출 (없으면 비움) |

3. 파일을 `marts/manual_ad_data/` 에 커밋(push)하면 워크플로 **"광고 어드민 RAW 적재"**가 자동 실행되어 적재됩니다. (수동 실행도 가능)

## 매체별 export 팁 (컬럼 매핑)
- **Meta 광고관리자**: 보고서 → 일별, 열=노출·클릭·지출금액·구매수·구매전환값. `지출금액`→cost, `구매수`→conversions.
- **Google Ads**: 캠페인 보고서 일별, 노출수·클릭수·비용·전환수·전환값.
- **네이버 검색광고**: 보고서 다운로드, 노출·클릭·총비용·전환수·전환매출.
- **카카오모먼트**: (이미 API로 1년 수집됨 — 필요 시에만)

## 멱등성
`(media, mall, report_date, campaign_name)` 기준 MERGE라 같은 파일을 다시 올려도 중복되지 않고 갱신됩니다.

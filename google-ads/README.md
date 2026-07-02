# Google Ads → BigQuery 데이터 파이프라인

egnis(클룹) 관리자 계정(MCC) 아래 모든 광고 계정의 성과를
BigQuery(`rf-ads-db-500505.google_ads`)에 자동으로 쌓습니다.

## 만들어지는 테이블 2개
- **campaign_daily** : 모든 캠페인의 `일별 × 기기별` 성과
  (컬럼: date, customer_id, customer_name, campaign_id, campaign_name,
  campaign_status, channel_type, device, impressions, clicks, cost,
  conversions, conversions_value, loaded_at)
- **keyword_daily** : 검색광고(GSA)의 `키워드별 × 기기별` 성과
  (컬럼: date, customer_id, customer_name, campaign_id, campaign_name,
  ad_group_id, ad_group_name, keyword_text, match_type, device,
  impressions, clicks, cost, conversions, conversions_value, loaded_at)

> device 값: MOBILE / DESKTOP / TABLET / CONNECTED_TV / OTHER
> match_type 값: EXACT / PHRASE / BROAD

## 동작 방식
- **backfill** : 최근 365일치 한 번에 적재 (최초 1회, 수동 실행)
- **daily** : 매일 새벽(KST 05:00) 어제 하루치 적재
- **hourly** : 매시 정각 오늘 데이터 갱신(덮어쓰기)
- 중복 방지: 넣을 날짜 구간을 먼저 지우고 다시 넣습니다. (두 테이블 모두)

## 준비물 (GitHub Secrets에 등록)
저장소 → Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 |
|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | 애즈 API 센터의 개발자 토큰 |
| `GOOGLE_ADS_CLIENT_ID` | OAuth 클라이언트 ID |
| `GOOGLE_ADS_CLIENT_SECRET` | OAuth 클라이언트 시크릿 |
| `GOOGLE_ADS_REFRESH_TOKEN` | 리프레시 토큰 |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | `3030273599` (MCC ID, 하이픈 없이) |
| `GCP_SA_KEY` | 서비스 계정 JSON 키 파일 **전체 내용** 붙여넣기 |

> ⚠️ 이 값들은 코드나 채팅에 절대 적지 말고 오직 Secrets에만 넣으세요.

## 실행 순서
1. 위 6개 Secret 등록
2. **기본 액세스(Basic access) 승인**이 나야 실제 데이터가 들어옵니다.
   (테스트 액세스 상태면 실제 계정 데이터는 에러가 납니다.)
3. GitHub → Actions 탭 → **"Backfill"** 워크플로우를 수동 실행해 1년치 채우기
4. 이후 daily / hourly 는 자동으로 돌아갑니다.

## 로컬 테스트 (선택)
```bash
pip install -r requirements.txt
export GOOGLE_ADS_DEVELOPER_TOKEN=...   # 나머지 환경변수도 동일하게
export GOOGLE_ADS_USE_PROTO_PLUS=True
export GOOGLE_APPLICATION_CREDENTIALS=./sa.json
python pipeline.py daily
```

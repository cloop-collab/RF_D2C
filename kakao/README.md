# Kakao Moment → BigQuery

카카오모먼트 광고계정의 **일자별 성과**를 BigQuery에 적재합니다. (타 매체와 네이밍·구조 통일)

## 테이블 (데이터셋 `kakao_moment`)
| 테이블 | 단위 | 내용 |
|---|---|---|
| `rf_kakao_campaign` | **캠페인** | campaign_id/name · 노출·클릭·비용 (타 매체와 통일, 통합 마트가 사용) |
| `rf_kakao_adgroup` | **광고그룹** | adgroup_id/name(+campaign_id) · 노출·클릭·비용 |
| `rf_kakao_moment` | 소재형식(DA) | `creative_format` 에 `image` 포함 — 노출·클릭·비용 |
| `rf_kakao_message` | 소재형식(메시지) | `creative_format` 에 `message` 포함 — **비용만** |

- 수집 방식:
  - 캠페인: 계정 리포트 `level=CAMPAIGN` + **캠페인 목록 API**(`/campaigns`)로 이름 매핑.
  - 광고그룹: 캠페인별 **광고그룹 목록 API**(`/adGroups`)로 ID 수집 → `adGroups/report`(ID 최대 40개 배치).
  - 소재형식: 계정 리포트 `dimension=CREATIVE_FORMAT`, 파생 `ad_type`(`DISPLAY`/`MESSAGE`/`OTHER`).
- **레이트리밋**: 다건 조회는 앱당 5초 1회 → 리포트 호출 전 6초 대기, 목록은 1.5초, 429/5xx 재시도.
- 프로젝트 `rf-ads-db-500505` / 위치 `asia-northeast3`, 날짜 **KST 기준**, 로드 잡(멱등: 기간 삭제 후 적재).

## 실행
```bash
python kakao_to_bigquery.py daily      # 어제 하루치
python kakao_to_bigquery.py backfill   # 최근 N일(BACKFILL_DAYS, 기본 365) — 31일 제한 자동 분할
```
- 자동: 워크플로 `Kakao Moment to BigQuery Daily`(`.github/workflows/kakao_daily.yml`), KST 06시대(누락 대비 중복 cron).
- 정시성: 검증 후 Cloud Scheduler 잡 `rf-kakao-daily` 추가 권장(§ `cloud-scheduler/`).

## GitHub Secrets
| 이름 | 값/설명 |
|---|---|
| `KAKAO_REST_API_KEY` | 앱 REST API 키(client_id). 예: `dfe7c03b...` |
| `KAKAO_REFRESH_TOKEN` | 최초 시드용 refresh token (`get_initial_token.py`로 발급). 이후 BQ가 자동 갱신 |
| `KAKAO_CLIENT_SECRET` | 앱 client_secret (앱에서 '사용 ON'일 때만) |
| `KAKAO_AD_ACCOUNT_IDS` | 광고계정 ID(콤마): `501057,800005` (클룹 501057 · 스프린트 800005) |
| `GCP_SA_KEY` | (기존) BigQuery 쓰기용 서비스계정 |

> 인증(2026-07 보완): 정적 `KAKAO_ACCESS_TOKEN`은 ~6시간이면 만료되므로 **refresh token 자동갱신**으로 전환.
> 토큰 상태는 `kakao_moment.oauth_state`에 저장. 최초 refresh token은 `python get_initial_token.py`로 발급.

## API (확정)
- 문서: https://developers.kakao.com/docs/ko/kakaomoment/report
- `GET https://apis.moment.kakao.com/openapi/v4/adAccounts/report`
- 헤더: `Authorization: Bearer <토큰>`, `adAccountId: <ID>`
- 파라미터: `adAccountId`, `start`/`end`(yyyyMMdd, 31일 이내), `metricsGroup=BASIC`, `dimension=CREATIVE_FORMAT`, `timeUnit=DAY`
- 응답: `data[].{ start, end, dimensions.creative_format, metrics.{imp, click, ctr, cost} }`

## 남은 것
- **모먼트 개발자 토큰이 심사 중** → 승인되면 위 2개 Secret 등록 후 자동 적재(코드 수정 불필요).
- 승인 후 실제 응답에서 **메시지가 함께 오는지 / DA만 오는지** 확인 — `ad_type` + 2테이블 구조라 어느 쪽이든 대응됨.
- (확장) 캠페인·광고그룹·소재 단위가 필요하면 엔티티 목록 API로 ID 수집 후 `campaigns/report`·`creatives/report`(ID 최대 5·100개)로 세분화 가능.

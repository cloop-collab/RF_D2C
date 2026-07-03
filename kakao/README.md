# Kakao Moment → BigQuery

카카오모먼트 광고계정의 **일자별 성과**를 BigQuery에 적재합니다. (타 매체와 네이밍·구조 통일)

## 테이블 (데이터셋 `kakao_moment`)
| 테이블 | 내용 | 구분 기준 |
|---|---|---|
| `rf_kakao_moment` | 디스플레이(비즈보드, DA) — 노출·클릭·비용 | `creative_format` 에 `image` 포함 |
| `rf_kakao_message` | 메시지(CRM) — **비용만** | `creative_format` 에 `message` 포함 |

- 수집 단위: **광고계정 × 소재형식(creative_format) × 일자** (계정 리포트 + `dimension=CREATIVE_FORMAT`).
- 각 행에 원본 `creative_format` + 파생 `ad_type`(`DISPLAY`/`MESSAGE`/`OTHER`).
- **비용 일자별 트래킹**: DA는 `rf_kakao_moment`, 메시지 비용은 `rf_kakao_message` — 물리 분리라 혼입 없음(제외 조건 불필요).
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
| `KAKAO_ACCESS_TOKEN` | 카카오모먼트 비즈니스 액세스 토큰 |
| `KAKAO_AD_ACCOUNT_IDS` | 광고계정 ID(콤마): `501057,800005` (클룹 501057 · 스프린트 800005) |
| `GCP_SA_KEY` | (기존) BigQuery 쓰기용 서비스계정 |

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

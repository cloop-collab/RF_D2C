# Kakao Moment → BigQuery

카카오모먼트 광고계정의 **일자별 성과**를 BigQuery에 적재합니다. (타 매체와 네이밍·구조 통일)

## 테이블 (데이터셋 `kakao_moment`)
| 테이블 | 내용 | 구분 기준 |
|---|---|---|
| `rf_kakao_moment` | 디스플레이(비즈보드, DA) — 전체 성과 | `creative_format` 에 `image` 포함 |
| `rf_kakao_message` | 메시지(CRM) — **비용만** 추적 | `creative_format` 에 `message` 포함 |

- 각 행에 원본 `creative_format` + 파생 `ad_type`(`DISPLAY`/`MESSAGE`/`OTHER`) 저장.
- **비용 일자별 트래킹 시**: DA만 보려면 `rf_kakao_moment`, 메시지 비용은 `rf_kakao_message` — 물리적으로 분리돼 있어 혼입 없음.
- 프로젝트 `rf-ads-db-500505` / 위치 `asia-northeast3`, 날짜 **KST 기준**, 로드 잡(멱등: 해당 기간 삭제 후 적재).

## 실행
```bash
python kakao_to_bigquery.py daily      # 어제 하루치
python kakao_to_bigquery.py backfill   # 최근 N일(BACKFILL_DAYS, 기본 365)
```
- 자동: 워크플로 `Kakao Moment to BigQuery Daily`(`.github/workflows/kakao_daily.yml`), KST 06시대.
- 정시성: 승인·검증 후 Cloud Scheduler 잡 `rf-kakao-daily` 추가 권장(§ cloud-scheduler).

## 필요한 GitHub Secrets
| 이름 | 설명 |
|---|---|
| `KAKAO_ACCESS_TOKEN` | 카카오모먼트 액세스 토큰 |
| `KAKAO_AD_ACCOUNT_IDS` | 광고계정 ID(콤마 구분, 예: `123,456`) |
| `GCP_SA_KEY` | (기존) BigQuery 쓰기용 서비스계정 |

## ⚠️ 토큰 승인 후 확정할 것 (`※확인`)
현재는 **스캐폴드**입니다. 네이밍/테이블/날짜/적재 로직은 완성이며, **카카오 API 스펙만** 확정하면 됩니다.
`kakao_to_bigquery.py` 안 `※확인` 표시 지점:
1. `REPORT_PATH` — 리포트 엔드포인트
2. `fetch_report()` params — 날짜 파라미터/포맷, `metricsGroup`, `dimension`
3. 응답 구조(`data` / `dimensions` / `metrics`)와 필드명(`creativeFormat`, `imp`, `click`, `cost`, `conv` 등)

> 참고: 카카오모먼트에 메시지가 API로 함께 내려오는지, 아니면 DA만 내려오는지는 승인 후 실제 응답으로 확인. `ad_type` 컬럼과 2테이블 구조라 어느 쪽이 와도 그대로 대응됩니다.

공식 문서: https://developers.kakao.com/docs/latest/ko/kakaomoment/common

# CRM 발송대상 추출 (온디맨드)

주문 행동으로 대상을 뽑고 → 회원 연락처를 그때그때 조회해 **발송리스트(CSV)**를 만듭니다.
**개인정보(이름·연락처)는 BigQuery/코드에 저장하지 않습니다.** 발송은 기존 CRM/메시지 툴에서 진행하세요.

## 흐름
```
[cafe24] 주문+주문상품 → 조건(상품·기간·최소주문수) 충족 member_id 세그먼트  (개인정보 없음)
   → 각 회원 /customersprivacy 온디맨드 조회 (이름·휴대폰·이메일·수신동의)      (개인정보)
   → 마케팅 수신동의(sms/news_mail) 필터
   → send_list.csv  (BQ 미저장, 아티팩트 1일 보관)
```

## 실행 (GitHub Actions)
Actions → **"CRM 발송대상 추출 (온디맨드)"** → Run workflow, 입력:
| 입력 | 설명 | 예 |
|---|---|---|
| `product_no` | 상품번호(콤마) | `472,401` |
| `product_kw` | 상품명 키워드(부분일치) | `애사비` |
| `days` | 최근 N일 내 구매 | `30` |
| `mall` | cloop / sprint / all | `all` |
| `min_orders` | 기간 내 최소 주문수 | `1` |
| `channel` | 수신동의 필터 sms/email/all | `all` |

- `product_no` 또는 `product_kw` 중 **하나는 필수**(둘 다 주면 둘 중 하나라도 매칭).
- 완료되면 실행 페이지 하단 **Artifacts → `crm-send-list`** 에서 `send_list.csv` 다운로드(1일 후 자동 삭제).

## 출력 컬럼
`member_id, mall, name, cellphone, email, sms_agree, email_agree, products, last_order_date, days_since_order, order_count`
→ 개인화 변수(이름·상품·경과일수·주문횟수) 포함. 문자/알림톡은 `cellphone`, 이메일은 `email` 사용.

## 로컬 실행(선택)
```bash
CRM_PRODUCT_KW="애사비" CRM_DAYS=30 python crm/crm_extract.py   # send_list.csv 로컬 생성
```
(BigQuery 접근 위해 gcloud ADC 필요. 개인정보 CSV는 로컬에만 남김.)

## 개인정보·컴플라이언스
- **저장 안 함**: 연락처는 발송 대상만 그때그때 조회, 창고에 마스터 미보관.
- **수신동의 필터**: `sms`/`news_mail` 동의자만 포함(정보통신망법). 미동의·연락처없음은 제외 카운트로 로그.
- 아티팩트 1일 보관 후 자동 삭제. 다운로드한 CSV는 발송 후 안전 삭제 권장.

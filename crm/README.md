# CRM 발송대상 추출 (온디맨드)

주문 행동으로 대상을 뽑고 → 연락처를 그때그때 조회해 **발송리스트(CSV)**를 만듭니다.
**개인정보(이름·연락처)는 BigQuery/코드에 저장하지 않습니다.** 발송은 기존 CRM/메시지 툴에서 진행하세요.

## 흐름
```
[cafe24] 주문+주문상품 → 조건 충족 대상 세그먼트 (개인정보 없음)
   ├ 회원(member_id 있음)   → /customersprivacy 로 이름·휴대폰·이메일·수신동의
   └ 비회원(게스트)         → /orders embed=receivers 로 수령자 이름·휴대폰 (수신동의 플래그 없음)
   → 수신동의 필터(회원) / 게스트는 '동의 별도 확보' 표기
   → send_list.csv  (BQ 미저장, 아티팩트 1일 보관)
```

## 조건 (GitHub Actions 입력)
Actions → **"CRM 발송대상 추출 (온디맨드)"** → Run workflow:
| 입력 | 설명 | 예 |
|---|---|---|
| `product_kw` | 대상상품 상품명 키워드 | `애사비` |
| `product_no` | 대상상품 상품번호(콤마) | `472,401` |
| `option_include` | 이 옵션만 포함(옵션값 부분일치) | `혼합 12개입` |
| `option_exclude` | 이 옵션 제외 | `단품` |
| `exclude_product_kw` | 이 상품 산 **회원 제외** | `화이바` |
| `coupon` | `any` / `used` / `notused` | `used` |
| `min_orders` | 기간 내 최소 주문수(회원, 재구매 타겟) | `2` |
| `days` | 최근 N일 내 구매 | `30` |
| `include_guest` | 비회원 포함(모수용) `1` | `1` |
| `channel` | 회원 수신동의 필터 `sms`/`email`/`all` | `all` |

- `product_no` 또는 `product_kw` 중 **하나는 필수**.
- 완료 후 실행 페이지 하단 **Artifacts → `crm-send-list`** 에서 `send_list.csv` 다운로드(1일 후 자동삭제).
- **고급/추가 조건**(`CRM_MALL`, `CRM_MIN_SPEND`, `CRM_EXCLUDE_PRODUCT_NO`)은 로컬 실행 시 env 로 지정 가능.

## 출력 컬럼
`customer_type, mall, ref, name, cellphone, email, sms_agree, email_agree, consent_note, products, last_order_date, days_since_order, order_count, spend, used_coupon`
- `customer_type`: member / guest, `ref`: 회원=member_id·게스트=order_id
- 개인화 변수: 이름·상품·경과일수·주문횟수·결제액. 문자/알림톡=`cellphone`, 이메일=`email`.

## 조건별 가능 범위 (현재 데이터 기준)
| 조건 | 회원 | 비회원(게스트) |
|---|---|---|
| 상품·옵션·기간·쿠폰사용 | ✅ | ✅(주문 단위) |
| 최소주문수·결제액합계·B상품 미구매 | ✅ | ❌(교차주문 식별 불가) |
| 연락처 | ✅ (회원 연락처) | ✅ (수령자 연락처) |
| 발송채널·동의 | **알림톡(정보성) → 수신동의 무관** | **LMS(광고성) → 수신동의 필수** |

## 발송채널·수신동의 규칙 (중요)
- **회원 → 알림톡(정보성)**: 수신동의 **무관** → 기본 무필터로 전체 추출. (광고성으로 보낼 때만 `CRM_MEMBER_CONSENT=1` 로 sms/news_mail 동의 필터)
- **비회원 → LMS(광고성)**: 수신동의 **필수**. 그런데 **주문 데이터에 수신동의 정보가 없음**(주문 72개 필드에 없음). → 도구는 게스트 모수·연락처를 뽑아주되, **발송 전 외부 옵트인 목록(문자플랫폼 등)으로 반드시 대조·필터**해야 합니다. `consent_note`·`send_channel` 컬럼으로 표기됩니다.

## 개인정보·컴플라이언스
- **저장 안 함**: 연락처는 발송 대상만 그때그때 조회, 창고에 마스터 미보관. 아티팩트 1일 후 자동삭제.
- `send_channel`(알림톡/LMS)·`consent_note` 컬럼으로 발송 방식·동의 요건을 각 행에 표기.
- 다운로드한 CSV는 발송 후 안전 삭제 권장.
- 다운로드한 CSV는 발송 후 안전 삭제 권장.

## 로컬 실행(선택)
```bash
CRM_PRODUCT_KW="애사비" CRM_DAYS=30 CRM_COUPON=used python crm/crm_extract.py
```
(BigQuery 접근 위해 gcloud ADC 필요. 개인정보 CSV는 로컬에만 남김.)

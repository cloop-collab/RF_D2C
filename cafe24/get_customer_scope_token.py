#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카페24 앱에 '개인정보(Privacy) 읽기(mall.read_privacy)' 스코프를 추가한 뒤,
그 스코프가 포함된 새 refresh token 을 발급받는 1회용 도우미 (로컬 실행 전용).

배경:
  - 가입수(신규회원)는 통계 API에 없고, 관리 API `/customersprivacy` 로만 조회 가능.
  - 이 엔드포인트는 `mall.read_privacy`(개인정보 Privacy) 스코프가 필요.
  - 스코프는 '재동의(re-consent)' 해야 토큰에 반영됨(단순 refresh로는 안 붙음).

사전 준비 (카페24 개발자센터 > 내 앱):
  1) '권한(Scope)' 에 `개인정보(Privacy) 읽기(mall.read_privacy)` 추가 후 저장.
  2) 'Redirect URI' 에 아래 REDIRECT_URI 값이 등록돼 있어야 함(기존 값 그대로 사용 가능).

사용법:
  CAFE24_MALL_ID=cloop CAFE24_CLIENT_ID=... python get_customer_scope_token.py
  (client_secret 은 실행 중 안전 입력)
  → 출력된 authorize URL 을 '몰 운영자 계정으로 로그인된 브라우저'에서 열고 동의
  → 이동된 전체 URL 붙여넣기 → 새 refresh_token 출력
  → GitHub Secret `CAFE24_REFRESH_TOKEN` 을 새 값으로 교체(스코프 포함 토큰).
"""
import os
import sys
import base64
import getpass
import urllib.parse
import urllib.request
import urllib.error

MALL_ID = os.environ.get("CAFE24_MALL_ID", "cloop").strip()
CLIENT_ID = os.environ.get("CAFE24_CLIENT_ID", "").strip()
# 개발자센터 앱에 등록된 Redirect URI 와 정확히 일치해야 함(필요시 환경변수로 덮어쓰기).
REDIRECT_URI = os.environ.get(
    "CAFE24_REDIRECT_URI", "https://github.com/cloop-collab/RF_D2C").strip()
# 기존 스코프 + 개인정보(Privacy) 읽기(재동의는 전체 스코프를 다시 요청).
# customersprivacy(가입수)에 필요한 것은 mall.read_privacy (개인화정보 mall.read_personal 아님).
SCOPE = os.environ.get(
    "CAFE24_SCOPE",
    "mall.read_order,mall.read_product,mall.read_customer,mall.read_analytics,mall.read_privacy",
).strip()

AUTHORIZE = f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/authorize"
TOKEN = f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token"


def main():
    if not CLIENT_ID:
        print("!! CAFE24_CLIENT_ID 환경변수가 필요합니다.")
        sys.exit(1)

    auth_url = (f"{AUTHORIZE}?response_type=code&client_id={CLIENT_ID}"
                f"&state=rfd2c&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
                f"&scope={urllib.parse.quote(SCOPE, safe=',')}")
    print("\n[1] 몰 운영자 계정으로 로그인된 브라우저에서 아래 URL 을 여세요(개인정보 읽기 동의 포함):\n")
    print(auth_url)
    print("\n[2] 동의 후 이동된 'https://.../callback?code=...' 주소창 전체 URL 을 붙여넣으세요.")
    redirected = input("\n이동된 전체 URL: ").strip()

    params = urllib.parse.parse_qs(urllib.parse.urlparse(redirected).query)
    if "code" not in params:
        print("\n!! URL에서 code를 찾지 못했습니다. error:",
              params.get("error"), params.get("error_description"))
        sys.exit(1)
    code = params["code"][0]

    # 시크릿은 미리 환경변수(CAFE24_CLIENT_SECRET)에 넣어두면 즉시 교환(코드 만료 방지).
    secret = os.environ.get("CAFE24_CLIENT_SECRET", "").strip()
    if not secret:
        secret = getpass.getpass("\n[3] Client Secret 입력(화면에 안 보임): ").strip()
    basic = base64.b64encode(f"{CLIENT_ID}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        TOKEN, data=body, method="POST",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        import json
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        print("\n!! 토큰 요청 실패:", e.code, e.read().decode()[:400])
        sys.exit(1)

    rt = tok.get("refresh_token")
    scopes = tok.get("scopes") or tok.get("scope")
    print("\n===== 결과 =====")
    print("발급 스코프:", scopes)
    if rt:
        print("\n아래 refresh_token 을 GitHub Secret 'CAFE24_REFRESH_TOKEN' 에 교체 저장하세요:\n")
        print("  " + str(rt))
        print("\n(다음 파이프라인 실행부터 oauth_state 가 이 토큰으로 갱신되어 개인정보 스코프가 붙습니다.)")
        if scopes and "mall.read_privacy" not in str(scopes):
            print("\n⚠️ 발급 스코프에 mall.read_privacy 가 없습니다 — 개발자센터에서 '개인정보(Privacy)' 추가 후 다시 동의하세요.")
    else:
        print("refresh_token 이 응답에 없습니다. 응답 전체:", tok)


if __name__ == "__main__":
    main()

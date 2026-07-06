#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카카오모먼트 최초 refresh token 발급 도우미 (1회용, 로컬 실행 전용).

* Client Secret/토큰은 이 PC 안에서만 처리되며 외부(채팅 등)로 전송되지 않습니다.
* 표준 라이브러리만 사용.

사전 준비 (카카오 개발자센터, 앱 736107):
  1) '카카오 로그인' 활성화 ON
  2) '카카오 로그인 > Redirect URI' 에 아래 값 등록: https://localhost/callback
  3) 카카오모먼트 비즈니스 동의항목이 승인돼 있어야 함(이미 승인됨)

사용법:
  python get_initial_token.py
  안내에 따라 (1) 인증 URL을 카카오모먼트 광고계정 접근 권한 있는 카카오계정으로 열고 동의 →
  (2) 이동된 주소창 전체 URL 붙여넣기 → (3) Client Secret(사용 ON일 때만) 입력.
  출력된 refresh_token 을 GitHub Secret KAKAO_REFRESH_TOKEN 에 저장.
"""
import sys
import json
import getpass
import urllib.parse
import urllib.request
import urllib.error

REST_API_KEY = "dfe7c03b6788bc3fb50605a8f79a65e4"   # 앱 736107 REST API 키(client_id)
REDIRECT_URI = "https://localhost/callback"
AUTHORIZE = "https://kauth.kakao.com/oauth/authorize"
TOKEN = "https://kauth.kakao.com/oauth/token"


def main():
    auth_url = (f"{AUTHORIZE}?response_type=code&client_id={REST_API_KEY}"
                f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}")
    print("\n[1] 카카오모먼트 접근 권한 있는 카카오계정으로 로그인된 브라우저에서 아래 URL을 여세요:\n")
    print(auth_url)
    print("\n    (동의 화면이 나오면 모두 동의)")
    print("[2] 동의 후 이동된 'https://localhost/callback?code=...' 주소창 전체 URL을 붙여넣으세요.")
    redirected = input("\n이동된 전체 URL: ").strip()

    params = urllib.parse.parse_qs(urllib.parse.urlparse(redirected).query)
    if "code" not in params:
        print("\n!! URL에서 code를 찾지 못했습니다.  error:",
              params.get("error"), params.get("error_description"))
        sys.exit(1)
    code = params["code"][0]

    secret = getpass.getpass("\n[3] Client Secret (앱에서 사용 ON일 때만, 아니면 그냥 Enter): ").strip()

    body = {"grant_type": "authorization_code", "client_id": REST_API_KEY,
            "redirect_uri": REDIRECT_URI, "code": code}
    if secret:
        body["client_secret"] = secret
    req = urllib.request.Request(
        TOKEN, data=urllib.parse.urlencode(body).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        print("\n!! 토큰 요청 실패:", e.code, e.read().decode()[:400])
        sys.exit(1)

    print("\n===== 발급 완료 =====")
    print("아래 refresh_token 을 GitHub Secret 'KAKAO_REFRESH_TOKEN' 에 저장하세요:\n")
    print("  " + str(tok.get("refresh_token")))
    print("\n(access_token 은 파이프라인이 자동 관리하므로 저장 안 해도 됩니다.)")
    print("refresh_token 유효기간: 약 2개월. 이후 파이프라인이 BigQuery에서 자동 갱신합니다.")


if __name__ == "__main__":
    main()

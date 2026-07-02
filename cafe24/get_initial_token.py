#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카페24 최초 refresh token 발급 도우미 (1회용, 로컬 실행 전용).

* Client Secret / 토큰은 이 PC 안에서만 처리되며, 외부(채팅 등)로 전송되지 않습니다.
* 표준 라이브러리만 사용 (설치 불필요).

사용법:
  1) 카페24 쇼핑몰 관리자(cloop.cafe24.com/admin)에 **대표운영자**로 로그인해 두기.
  2) 이 스크립트 실행.
  3) 출력된 인증 URL을 같은 브라우저에서 열고 '동의/승인'.
  4) 이동된 github 페이지의 주소창 전체 URL을 복사해 붙여넣기.
  5) Client Secret 입력(화면에 안 보임).
  6) 출력된 refresh_token 을 GitHub Secret `CAFE24_REFRESH_TOKEN` 에 저장.
"""
import sys
import json
import base64
import getpass
import urllib.parse
import urllib.request
import urllib.error

MALL_ID = "cloop"
CLIENT_ID = "kJ6IyWGJHFnojIbZkcVccA"
REDIRECT_URI = "https://github.com/cloop-collab/RF_D2C"
SCOPE = "mall.read_order,mall.read_product,mall.read_customer,mall.read_analytics"


def main():
    auth_url = (
        f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}&state=rfd2c"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
        f"&scope={SCOPE}"
    )
    print("\n[1] 카페24 대표운영자로 로그인된 브라우저에서 아래 인증 URL을 여세요:\n")
    print(auth_url)
    print("\n[2] '동의/승인' 후 이동된 github 페이지의 주소창 '전체 URL'을 복사해 붙여넣으세요.")
    redirected = input("\n이동된 전체 URL 붙여넣기: ").strip()

    params = urllib.parse.parse_qs(urllib.parse.urlparse(redirected).query)
    if "code" not in params:
        print("\n!! URL에서 code를 찾지 못했습니다.")
        print("   error:", params.get("error"), params.get("error_description"))
        sys.exit(1)
    code = params["code"][0]

    secret = getpass.getpass("\n[3] Client Secret 입력 (화면에 표시되지 않습니다): ").strip()

    token_url = f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token"
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    basic = base64.b64encode(f"{CLIENT_ID}:{secret}".encode()).decode()
    req = urllib.request.Request(
        token_url, data=body, method="POST",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        print("\n!! 토큰 요청 실패:", e.code, e.read().decode()[:400])
        sys.exit(1)

    print("\n===== 발급 완료 =====")
    print("아래 refresh_token 을 GitHub Secret 'CAFE24_REFRESH_TOKEN' 에 저장하세요:\n")
    print("  " + str(tok.get("refresh_token")))
    print("\n(access_token 은 파이프라인이 자동 관리하므로 따로 저장하지 않아도 됩니다.)")
    print("만료 안내: refresh_token 은 2주간 유효하며, 첫 실행 후에는 BigQuery가 자동 갱신합니다.")


if __name__ == "__main__":
    main()

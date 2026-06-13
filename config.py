# -*- coding: utf-8 -*-
"""
API 키 로더 (배포용 - 키 미포함, 안전하게 공개 가능)
키는 Streamlit Secrets 또는 환경변수에서 읽음.
- 클라우드: Streamlit Cloud의 Secrets에 DATA_GO_KR_KEY, KAKAO_REST_KEY 등록
- 로컬: 환경변수로 설정하거나, 이 파일에 직접 넣지 말고 .streamlit/secrets.toml 사용
"""

import os


def _get(key):
    # 1) Streamlit Secrets
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    # 2) 환경변수
    return os.environ.get(key, "")


DATA_GO_KR_KEY = _get("DATA_GO_KR_KEY")
KAKAO_REST_KEY = _get("KAKAO_REST_KEY")

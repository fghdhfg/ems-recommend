# -*- coding: utf-8 -*-
"""
fire_bigdata.py
소방안전 빅데이터 플랫폼(bigdata-119.kr) API 모듈.

API (POST + 쿼리스트링 파라미터, 헤더 X-API-KEY, 응답 JSON):
  - 전국 구급 현황          : /fsdpApi/rest/v1/ems-incidents      (구급 출동 원시 레코드)
  - 전국 구급상황관리 현황  : /fsdpApi/rest/v1/ems-medical-consults (구급상황관리센터 의료상담)

★ 파라미터는 본문(JSON)이 아니라 '쿼리스트링'으로 보내야 함 (params=...) ★
  page(기본1), size(기본20, 최대100). 출력 필드명을 그대로 넣어 부분일치(ILIKE) 필터 가능.
응답 envelope: {"total": N, "totalPages": M, "page": p, "size": s, "items": [...]}

인증키는 코드에 박지 않음 → config.BIGDATA119_KEY (Secrets/환경변수).
"""

import os

import requests

try:
    from config import BIGDATA119_KEY
except Exception:
    BIGDATA119_KEY = os.environ.get("BIGDATA119_KEY", "")

BASE = "http://www.bigdata-119.kr/fsdpApi/rest/v1"
EMS_INCIDENTS = "ems-incidents"
EMS_MED_CONSULTS = "ems-medical-consults"


def _post(path, page=1, size=100, filters=None, key=None, timeout=30):
    key = key or BIGDATA119_KEY
    if not key:
        raise RuntimeError("BIGDATA119_KEY 없음 — Secrets/환경변수에 키를 설정하세요.")
    params = {"page": page, "size": size}
    if filters:
        params.update(filters)
    # ★ 파라미터는 쿼리스트링(params)으로 전달 (본문 JSON 아님)
    resp = requests.post(f"{BASE}/{path}",
                         headers={"X-API-KEY": key},
                         params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _records(obj):
    """레코드 리스트(items)를 뽑아낸다."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("items", "content", "data", "list", "rows"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
    return []


def _total(obj):
    """전체 건수(total)를 뽑아낸다. 없으면 None."""
    if isinstance(obj, dict):
        for k in ("total", "totalElements", "totalCount", "totalCnt", "count"):
            v = obj.get(k)
            if isinstance(v, int):
                return v
    return None


def total_count(path, filters=None, key=None, timeout=30):
    """필터 조건에 맞는 전체 건수만 싸게 조회 (size=1)."""
    return _total(_post(path, page=1, size=1, filters=filters, key=key, timeout=timeout))


def get_records(path, filters=None, page=1, size=100, key=None, timeout=30):
    return _records(_post(path, page=page, size=size, filters=filters, key=key, timeout=timeout))


# ── 편의 함수 ──
def seoul_incident_count(key=None):
    """서울 구급 출동(현황) 누적 건수."""
    return total_count(EMS_INCIDENTS, {"ctpvNm": "서울"}, key)


def seoul_consult_count(key=None):
    """서울 구급상황관리센터 의료상담 누적 건수."""
    return total_count(EMS_MED_CONSULTS, {"ctpvNm": "서울"}, key)


def national_incident_total(key=None):
    """전국 구급 출동 전체 건수 (필터 없음 → 빠름)."""
    return total_count(EMS_INCIDENTS, None, key)


def national_consult_total(key=None):
    """전국 구급상황관리 의료상담 전체 건수."""
    return total_count(EMS_MED_CONSULTS, None, key)


def inspect(path, key=None):
    """응답 envelope/필드 확인."""
    raw = _post(path, page=1, size=3, key=key)
    print(f"\n===== {path} =====")
    if isinstance(raw, dict):
        print("최상위 키:", list(raw.keys()))
        print("전체건수(total):", _total(raw))
    recs = _records(raw)
    print("레코드 수:", len(recs))
    if recs:
        print("-- 첫 레코드 --")
        for k, v in recs[0].items():
            print(f"   {k}: {v}")


if __name__ == "__main__":
    if not BIGDATA119_KEY:
        print("환경변수 BIGDATA119_KEY 를 먼저 설정하세요 (set/export).")
    else:
        for p in (EMS_INCIDENTS, EMS_MED_CONSULTS):
            try:
                inspect(p)
            except Exception as e:
                print(f"\n[{p}] 실패: {e}")
        print("\n전국 구급 출동 전체:", national_incident_total())
        print("전국 의료상담 전체:", national_consult_total())

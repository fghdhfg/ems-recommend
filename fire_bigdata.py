# -*- coding: utf-8 -*-
"""
fire_bigdata.py
소방안전 빅데이터 플랫폼(bigdata-119.kr) API 모듈.

API (POST, 헤더 X-API-KEY, 응답 JSON):
  - 전국 구급 현황          : /fsdpApi/rest/v1/ems-incidents      (구급 출동 원시 레코드)
  - 전국 구급상황관리 현황  : /fsdpApi/rest/v1/ems-medical-consults (구급상황관리센터 의료상담)
파라미터: page(기본1), size(기본20, 최대100), 그리고 '출력 필드명'을 그대로 넣어
          부분일치(ILIKE) 필터 가능 (예: ctpvNm="서울").

인증키는 코드에 박지 않음 → config.BIGDATA119_KEY (Secrets/환경변수).

응답 구조 확인:  python fire_bigdata.py   (환경변수 BIGDATA119_KEY 설정 후)
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


def _post(path, page=1, size=100, filters=None, key=None, timeout=15):
    key = key or BIGDATA119_KEY
    if not key:
        raise RuntimeError("BIGDATA119_KEY 없음 — Secrets/환경변수에 키를 설정하세요.")
    body = {"page": page, "size": size}
    if filters:
        body.update(filters)
    resp = requests.post(f"{BASE}/{path}",
                         headers={"X-API-KEY": key, "Content-Type": "application/json"},
                         json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _records(obj):
    """응답 envelope가 어떤 형태든 레코드 리스트를 뽑아낸다."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("content", "data", "items", "list", "result", "results", "rows"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = _records(v)
                if inner:
                    return inner
    return []


def _total(obj):
    """전체 건수(totalElements 등)를 뽑아낸다. 없으면 None."""
    keys = ("totalElements", "totalCount", "totalCnt", "total", "count", "totalRows")
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, int):
                return v
        for wrapper in ("data", "result", "page", "pageInfo", "paging"):
            w = obj.get(wrapper)
            if isinstance(w, dict):
                for k in keys:
                    v = w.get(k)
                    if isinstance(v, int):
                        return v
    return None


def total_count(path, filters=None, key=None):
    """필터 조건에 맞는 전체 건수만 싸게 조회 (size=1)."""
    return _total(_post(path, page=1, size=1, filters=filters, key=key))


def get_records(path, filters=None, page=1, size=100, key=None):
    return _records(_post(path, page=page, size=size, filters=filters, key=key))


# 편의 함수
def seoul_incident_count(key=None):
    """서울 구급 출동(현황) 누적 건수."""
    return total_count(EMS_INCIDENTS, {"ctpvNm": "서울"}, key)


def seoul_consult_count(key=None):
    """서울 구급상황관리센터 의료상담 누적 건수."""
    return total_count(EMS_MED_CONSULTS, {"ctpvNm": "서울"}, key)


def inspect(path, key=None):
    """최초 1회: 응답 envelope/필드 확인."""
    raw = _post(path, page=1, size=3, key=key)
    print(f"\n===== {path} =====")
    print("최상위 타입:", type(raw).__name__)
    if isinstance(raw, dict):
        print("최상위 키:", list(raw.keys()))
        print("전체건수(_total):", _total(raw))
    recs = _records(raw)
    print("레코드 수:", len(recs))
    if recs:
        print("-- 첫 레코드 --")
        for k, v in recs[0].items():
            print(f"   {k}: {v}")
    else:
        print("원문 앞부분:", str(raw)[:800])


if __name__ == "__main__":
    if not BIGDATA119_KEY:
        print("환경변수 BIGDATA119_KEY 를 먼저 설정하세요 (set/export).")
    else:
        for p in (EMS_INCIDENTS, EMS_MED_CONSULTS):
            try:
                inspect(p)
            except Exception as e:
                print(f"\n[{p}] 실패: {e}")
        print("\n서울 구급 출동 건수:", seoul_incident_count())
        print("서울 의료상담 건수:", seoul_consult_count())

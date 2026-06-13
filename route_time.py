# -*- coding: utf-8 -*-
"""
카카오모빌리티 길찾기: 현장 좌표 -> 병원 좌표 예상 소요시간/거리 조회

사용 예:
    from route_time import get_route_time
    r = get_route_time((126.9214, 37.6027), (126.9170, 37.6358))
    print(r)  # {'duration_min': 12.3, 'distance_km': 4.1}

실행 테스트:
    python route_time.py
"""

import sys

import requests

from config import KAKAO_REST_KEY

KAKAO_URL = "https://apis-navi.kakaomobility.com/v1/directions"


def get_route_time(origin: tuple, destination: tuple) -> dict | None:
    """
    origin, destination: (경도, 위도) 튜플  ※ 카카오는 '경도,위도' 순서!
    반환: {'duration_min': 분, 'distance_km': km} 또는 경로 없음/오류 시 None
    """
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "priority": "TIME",  # 최단시간 우선 (구급차니까)
    }
    resp = requests.get(KAKAO_URL, headers=headers, params=params, timeout=10)
    if resp.status_code != 200:
        print(f"[route_time] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None

    data = resp.json()
    routes = data.get("routes", [])
    if not routes or routes[0].get("result_code", -1) != 0:
        return None  # 경로 탐색 실패

    summary = routes[0]["summary"]
    # 경로 좌표(폴리라인) 추출: sections > roads > vertexes (경도,위도 평탄화 배열)
    path = []
    for section in routes[0].get("sections", []):
        for road in section.get("roads", []):
            v = road.get("vertexes", [])
            for i in range(0, len(v) - 1, 2):
                path.append([v[i], v[i + 1]])   # [경도, 위도]
    return {
        "duration_min": round(summary["duration"] / 60, 1),
        "distance_km": round(summary["distance"] / 1000, 1),
        "path": path,
    }


if __name__ == "__main__":
    # 테스트: 은평구 불광역 인근 -> 은평성모병원 인근
    origin = (126.9300, 37.6105)
    dest = (126.9170, 37.6340)
    result = get_route_time(origin, dest)
    if result:
        print(f"OK! 예상 {result['duration_min']}분, {result['distance_km']}km")
    else:
        print("경로 조회 실패 - 카카오모빌리티 활성화 여부를 확인하세요.")

# -*- coding: utf-8 -*-
"""
응급의료기관 API (국립중앙의료원) 조회 모듈
1) get_er_beds()      : 특정 구의 실시간 가용병상 조회
2) get_er_locations() : 응급의료기관 목록 + 좌표(위도/경도) 조회

실행 테스트:
    python er_live.py
"""

import xml.etree.ElementTree as ET

import requests

from config import DATA_GO_KR_KEY

BASE = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"


def _call(operation: str, params: dict) -> ET.Element:
    p = {"serviceKey": DATA_GO_KR_KEY, "pageNo": 1, "numOfRows": 100}
    p.update(params)
    resp = requests.get(f"{BASE}/{operation}", params=p, timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    code = root.findtext(".//resultCode")
    if code != "00":
        raise RuntimeError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")
    return root


def get_er_beds(sido: str = "서울특별시", sigungu: str | None = None) -> list[dict]:
    """실시간 가용병상. sigungu 생략 시 시도 전체."""
    params = {"STAGE1": sido}
    if sigungu:
        params["STAGE2"] = sigungu
    root = _call("getEmrrmRltmUsefulSckbdInfoInqire", params)

    hospitals = []
    for item in root.iter("item"):
        hospitals.append({
            "hpid": item.findtext("hpid", ""),
            "name": item.findtext("dutyName", ""),
            "er_beds": _to_int(item.findtext("hvec")),       # 응급실 일반병상 잔여
            "er_beds_total": _to_int(item.findtext("hvs01")), # 총 병상
            "op_rooms": _to_int(item.findtext("hvoc")),       # 수술실
            "ct": item.findtext("hvctayn", ""),               # CT 가용 (Y/N)
            "mri": item.findtext("hvmriayn", ""),             # MRI 가용
            "angio": item.findtext("hvangioayn", ""),         # 혈관촬영 (심근경색 시술)
            "venti": item.findtext("hvventiayn", ""),         # 인공호흡기
            "updated": item.findtext("hvidate", ""),          # 병원측 갱신시각
        })
    return hospitals


def get_er_locations(sido: str = "서울특별시", sigungu: str | None = None) -> list[dict]:
    """응급의료기관 목록 + 좌표. 길찾기 API에 넣을 도착지 좌표 소스."""
    params = {"Q0": sido}
    if sigungu:
        params["Q1"] = sigungu
    root = _call("getEgytListInfoInqire", params)

    hospitals = []
    for item in root.iter("item"):
        lat = item.findtext("wgs84Lat")
        lon = item.findtext("wgs84Lon")
        if not lat or not lon:
            continue
        hospitals.append({
            "hpid": item.findtext("hpid", ""),
            "name": item.findtext("dutyName", ""),
            "addr": item.findtext("dutyAddr", ""),
            "tel_er": item.findtext("dutyTel3", ""),  # 응급실 직통
            "lat": float(lat),
            "lon": float(lon),
        })
    return hospitals


# MKioskTy 코드 -> 의미 (활용가이드 기준)
MKIOSK_LABELS = {
    1: "심근경색 재관류", 2: "뇌경색 재관류", 3: "뇌출혈수술(거미막하)",
    4: "뇌출혈수술(거미막하 외)", 5: "대동맥응급(흉부)", 6: "대동맥응급(복부)",
    7: "담낭담관질환(담낭)", 8: "담낭담관질환(담도)", 9: "복부응급수술(비외상)",
    10: "장중첩/폐색(유아)", 11: "성인 위장관 응급내시경", 12: "영유아 위장관 응급내시경",
    13: "성인 기관지 응급내시경", 14: "영유아 기관지 응급내시경", 15: "저출생체중아",
    16: "산부인과 응급(분만)", 17: "산부인과 응급(산과수술)", 18: "산부인과 응급(부인과수술)",
    19: "중증화상", 20: "사지접합(수족지접합)", 21: "사지접합(수족지접합 외)",
    22: "응급투석(HD)", 23: "응급투석(CRRT)", 24: "정신과적 응급입원",
    25: "안과적 응급수술", 26: "성인 영상의학 혈관중재", 27: "영유아 영상의학 혈관중재",
}


def get_er_acceptance(sido: str = "서울특별시", sigungu: str | None = None) -> dict:
    """
    중증질환 수용가능 정보. 반환: {hpid: {1: 'Y', 2: 'N', ...}}
    MKioskTyN = Y(수용가능)/N(불가)/정보미제공
    """
    params = {"STAGE1": sido}
    if sigungu:
        params["STAGE2"] = sigungu
    root = _call("getSrsillDissAceptncPosblInfoInqire", params)

    out = {}
    for item in root.iter("item"):
        hpid = item.findtext("hpid", "")
        codes = {}
        for n in range(1, 28):
            val = (item.findtext(f"MKioskTy{n}") or "").strip()
            codes[n] = val
        out[hpid] = codes
    return out


# 수용불가/진료불가 공지 '본문'이 담길 만한 후보 태그명들.
# ⚠️ 라이브 응답으로 확정 못했으므로 후보키로 탐색한다.
#    python er_live.py 실행 시 하단에서 실제 item 태그를 덤프하니,
#    메시지 본문이 어느 태그인지 확인한 뒤 이 리스트를 그 이름으로 정리할 것.
#    (raw 딕셔너리에 모든 태그가 보존되므로 잘못 잡아도 데이터는 안 잃음.)
_MSG_TEXT_KEYS = ["symBlkMsg", "symTypCodMag", "symBlkMsgTyp", "msg", "message"]


def get_er_diss_messages(sido: str = "서울특별시", sigungu: str | None = None) -> list[dict]:
    """
    응급실이 띄운 '수용불가 / ○○과 진료불가' 실시간 공지 메시지.
    API: getEmrrmSrsillDissMsgInqire
    반환: [{"hpid":..., "name":..., "message":..., "raw":{태그:값}}, ...]

    ※ 특정 구(sigungu) 단독은 공지 0건일 때가 많다.
      앱에서는 시도 전체로 받아 hpid 로 매칭하는 것을 권장.
    ※ 공지 없음(NODATA)은 정상 상태이므로 빈 리스트를 반환한다(예외 X).
    """
    params = {"STAGE1": sido}
    if sigungu:
        params["STAGE2"] = sigungu
    try:
        root = _call("getEmrrmSrsillDissMsgInqire", params)
    except RuntimeError:
        # NODATA 등으로 resultCode != 00 인 경우 → 공지 없음으로 간주
        return []

    out = []
    for item in root.iter("item"):
        msg = ""
        for k in _MSG_TEXT_KEYS:
            v = (item.findtext(k) or "").strip()
            if v:
                msg = v
                break
        out.append({
            "hpid": item.findtext("hpid", ""),
            "name": item.findtext("dutyName", ""),
            "message": msg,
            "raw": {c.tag: (c.text or "").strip() for c in item},
        })
    return out


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    print("=== 은평구 실시간 가용병상 ===")
    for h in get_er_beds("서울특별시", "은평구"):
        full = ""
        if h["er_beds"] is not None and h["er_beds"] <= 0:
            full = "  <<< 포화!"
        print(f"  {h['name']}: 응급실 {h['er_beds']}/{h['er_beds_total']}석, "
              f"CT={h['ct']} MRI={h['mri']}{full}")

    print("\n=== 은평구 응급의료기관 좌표 ===")
    for h in get_er_locations("서울특별시", "은평구"):
        print(f"  {h['name']} ({h['lat']:.4f}, {h['lon']:.4f}) {h['addr']}")

    print("\n=== 은평구 중증질환 수용가능 (주요 항목) ===")
    acc = get_er_acceptance("서울특별시", "은평구")
    beds_name = {h["hpid"]: h["name"] for h in get_er_beds("서울특별시", "은평구")}
    for hpid, codes in acc.items():
        name = beds_name.get(hpid, hpid)
        key_items = []
        for n in (1, 2, 3, 19):  # 심근경색/뇌경색/뇌출혈/중증화상
            mark = "✓" if codes.get(n) == "Y" else "✗"
            key_items.append(f"{MKIOSK_LABELS[n]}={mark}")
        print(f"  {name}: " + ", ".join(key_items))

    print("\n=== 수용불가 메시지 (서울 전체에서 앞 5건 + 태그 확인) ===")
    msgs = get_er_diss_messages("서울특별시")   # 구 단독은 0건 많아 전체로 확인
    if not msgs:
        print("  현재 등록된 공지 0건 (정상일 수 있음)")
    else:
        # ★ 첫 건의 태그 목록 = 실제 응답 필드. _MSG_TEXT_KEYS 정리에 사용 ★
        print("  [첫 건 태그 목록]")
        for tag, val in msgs[0]["raw"].items():
            print(f"    {tag}: {val}")
        print("  [앞 5건 요약]")
        for m in msgs[:5]:
            print(f"    {m['name']}({m['hpid']}): {m['message'] or '(본문 태그 미확정 — raw 확인)'}")

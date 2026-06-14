# -*- coding: utf-8 -*-
"""
119 응급이송 응급실 추천 — 앱 v3
실행: python -m streamlit run app.py
   (최초 1회: python -m pip install streamlit pandas pydeck streamlit-geolocation)

v3 핵심
- 중증질환 수용가능(MKioskTy) 필터: "그 질환을 지금 받겠다고 선언한 병원"만 추천
- 현장 위치를 GPS / 주소검색 / 동 선택 3가지로 지정
- 환자 유형별 골든타임 목표시간 + 도착 가능 배지
"""

import os
import json
from datetime import datetime

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

from config import DATA_GO_KR_KEY, KAKAO_REST_KEY
from er_live import (get_er_beds, get_er_locations, get_er_acceptance,
                     MKIOSK_LABELS)
# 수용불가 메시지 기능: er_live.py가 구버전이어도 앱 전체가 죽지 않도록 옵션 import
try:
    from er_live import get_er_diss_messages
except ImportError:
    get_er_diss_messages = None
from route_time import get_route_time

# 페이지 아이콘: favicon.png 있으면 사용, 없으면 이모지 폴백
_icon = "🚑"
try:
    from PIL import Image
    _here = os.path.dirname(os.path.abspath(__file__))
    for _p in (os.path.join(_here, "favicon.png"), "favicon.png"):
        if os.path.exists(_p):
            _icon = Image.open(_p)
            break
except Exception:
    pass

st.set_page_config(page_title="수용ON — 응급실 추천", page_icon=_icon, layout="wide")

# ──────────────────────────────────────────────
# 환자 유형 정의
#   req_codes: 이 질환 수용가능(MKioskTy=Y)이 필요한 코드들 (하나라도 Y면 통과)
#   req_equip: 장비 필수조건 (가용병상 API)
#   target_min: 이송 목표시간(분)
# ──────────────────────────────────────────────
PATIENT_TYPES = {
    "일반":          {"req_codes": [],        "req_equip": {},                "target_min": None,
                      "note": ""},
    "심정지(CPR중)": {"req_codes": [],        "req_equip": {},                "target_min": 10,
                      "note": "분 단위가 생존율 좌우 — 최단시간 우선"},
    "심근경색 의심": {"req_codes": [1],       "req_equip": {},                "target_min": 30,
                      "note": "재관류중재술(MKioskTy1) 가능 병원으로 직행"},
    "뇌졸중 의심":   {"req_codes": [2, 3, 4], "req_equip": {"ct": "Y"},       "target_min": 30,
                      "note": "뇌경색 재관류 또는 뇌출혈수술 가능 + CT 필수"},
    "중증외상":      {"req_codes": [9, 20],   "req_equip": {"op_rooms_min": 1}, "target_min": 30,
                      "note": "복부응급수술/사지접합 등 + 수술실 가용"},
    "중증화상":      {"req_codes": [19],      "req_equip": {},                "target_min": 30,
                      "note": "중증화상 수용가능 병원 우선"},
    "대동맥 응급":   {"req_codes": [5, 6],    "req_equip": {},                "target_min": 30,
                      "note": "흉부/복부 대동맥응급 수용가능 병원"},
    "호흡곤란":      {"req_codes": [],        "req_equip": {"venti": "Y"},    "target_min": 20,
                      "note": "인공호흡기 가용 병원 우선"},
}

DONG_COORDS = {
    "불광동": (126.9300, 37.6105), "응암동": (126.9215, 37.5984),
    "녹번동": (126.9352, 37.6007), "갈현동": (126.9135, 37.6190),
    "역촌동": (126.9145, 37.6060), "구산동": (126.9070, 37.6105),
    "대조동": (126.9245, 37.6135), "신사동": (126.9095, 37.5915),
    "증산동": (126.9095, 37.5835), "진관동(은평뉴타운)": (126.9227, 37.6395),
}

DISTRICT_SETS = {
    "은평구만": ["은평구"],
    "인접 구 포함 (서대문·마포·종로)": ["은평구", "서대문구", "마포구", "종로구"],
}

SATURATION_PENALTY_MIN = 20   # 프로파일 없을 때 폴백용 고정 페널티(분)
SAT_MAX_PENALTY = 25          # 포화율 100%일 때 최대 페널티(분)
SAT_MIN_BUCKET_N = 5          # 시간대 버킷 신뢰 최소 표본; 미만이면 전체 포화율 사용
STALE_MIN = 60


# ──────────────────────────────────────────────
# 데이터
# ──────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def load_beds(districts):
    out = []
    for gu in districts:
        out.extend(get_er_beds("서울특별시", gu))
    return out


@st.cache_data(ttl=600, show_spinner=False)
def load_locations(districts):
    out, seen = [], set()
    for gu in districts:
        for h in get_er_locations("서울특별시", gu):
            if h["hpid"] not in seen:
                seen.add(h["hpid"])
                out.append(h)
    return out


@st.cache_data(ttl=120, show_spinner=False)
def load_acceptance(districts):
    out = {}
    for gu in districts:
        out.update(get_er_acceptance("서울특별시", gu))
    return out


@st.cache_data(ttl=180, show_spinner=False)
def load_diss_messages():
    """
    서울 전체 응급실 수용불가 공지 → {hpid: [메시지dict, ...]}.
    공지는 구 단독으로 0건이 많아 시도 전체로 받아 hpid로 매칭한다.
    메시지 API가 실패해도 추천 본기능은 죽지 않도록 빈 dict 반환.
    """
    try:
        idx = {}
        if get_er_diss_messages is None:      # er_live 구버전 → 기능 비활성
            return idx
        for m in get_er_diss_messages("서울특별시"):
            if m.get("hpid"):
                idx.setdefault(m["hpid"], []).append(m)
        return idx
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def geocode(address):
    """카카오 주소->좌표 (경도, 위도)"""
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    r = requests.get(url, headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
                     params={"query": address}, timeout=10)
    docs = r.json().get("documents", [])
    if not docs:
        # 키워드 검색으로 폴백 (건물명 등)
        url2 = "https://dapi.kakao.com/v2/local/search/keyword.json"
        r = requests.get(url2, headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
                         params={"query": address}, timeout=10)
        docs = r.json().get("documents", [])
    if not docs:
        return None
    d = docs[0]
    return (float(d["x"]), float(d["y"]))   # (lon, lat)


def freshness(hvidate):
    try:
        t = datetime.strptime(hvidate, "%Y%m%d%H%M%S")
        m = max(0, int((datetime.now() - t).total_seconds() // 60))
        return m, (f"{m}분 전" if m < 60 else f"{m // 60}시간 {m % 60}분 전")
    except (TypeError, ValueError):
        return None, "갱신시각 미상"


SAT_PROFILE_URL = ("https://raw.githubusercontent.com/fghdhfg/"
                   "EMS_colletcer/main/data/saturation_profile.json")


@st.cache_data(ttl=1800, show_spinner=False)
def load_saturation_profile():
    """
    수집 봇이 갱신하는 병원별 포화 프로파일.
    1) collector repo의 최신본을 가져오고(자동 반영),
    2) 실패하면 배포에 동봉한 로컬 saturation_profile.json,
    3) 그것도 없으면 None(고정 +20 폴백).
    """
    try:
        r = requests.get(SAT_PROFILE_URL, timeout=5)
        if r.status_code == 200 and r.text.strip():
            return r.json()
    except Exception:
        pass
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "saturation_profile.json")
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return None


def _bucket_now():
    return ["dawn", "morning", "afternoon", "night"][min(datetime.now().hour // 6, 3)]


def saturation_penalty(hpid, profile, live_saturated):
    """
    포화 페널티(분)와 사용한 포화율 반환.
    - 프로파일에 병원 있으면: (현재 시간대 버킷 표본 충분→버킷율, 아니면 전체율) × 최대페널티
    - 없으면: 기존 고정 로직(현재 포화면 +20) 폴백
    """
    if profile:
        h = profile.get("hospitals", {}).get(hpid)
        if h:
            b = h.get("buckets", {}).get(_bucket_now())
            rate = b["rate"] if (b and b["n"] >= SAT_MIN_BUCKET_N) else h["overall_rate"]
            return round(rate * SAT_MAX_PENALTY, 1), rate
    return (SATURATION_PENALTY_MIN if live_saturated else 0), None


def recommend(scene, ptype_name, districts, top_n=3):
    p = PATIENT_TYPES[ptype_name]
    beds = {h["hpid"]: h for h in load_beds(tuple(districts))}
    acc = load_acceptance(tuple(districts))
    sat_profile = load_saturation_profile()

    results = []
    for loc in load_locations(tuple(districts)):
        b = beds.get(loc["hpid"])
        if b is None:
            continue
        # 장비 필수조건
        eq = p["req_equip"]
        if eq.get("ct") and b["ct"] != "Y":
            continue
        if eq.get("venti") and b["venti"] != "Y":
            continue
        if eq.get("op_rooms_min") and (b["op_rooms"] or 0) < eq["op_rooms_min"]:
            continue
        # 중증질환 수용가능: 필요한 코드 중 하나라도 Y면 통과
        codes = acc.get(loc["hpid"], {})
        if p["req_codes"]:
            if not any(codes.get(c) == "Y" for c in p["req_codes"]):
                continue
        route = get_route_time(scene, (loc["lon"], loc["lat"]))
        if route is None:
            continue
        saturated = b["er_beds"] is not None and b["er_beds"] <= 0
        sat_pen, sat_rate = saturation_penalty(loc["hpid"], sat_profile, saturated)
        score = route["duration_min"] + sat_pen
        mago, ftxt = freshness(b.get("updated", ""))
        # 수용 가능한 질환 라벨 (이 환자유형 관련)
        accepted = [MKIOSK_LABELS[c] for c in p["req_codes"] if codes.get(c) == "Y"]
        results.append({**loc, **b, **route, "saturated": saturated, "score": score,
                        "sat_pen": sat_pen, "sat_rate": sat_rate,
                        "mago": mago, "ftxt": ftxt, "accepted": accepted})
    results.sort(key=lambda x: x["score"])
    return results[:top_n], results


# ──────────────────────────────────────────────
# 사이드바 — 현장 위치 지정 (GPS / 주소 / 동)
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚑 출동 정보")

    loc_mode = st.radio("현장 위치 지정", ["📍 현재 위치(GPS)", "🔎 주소 검색", "📋 동 선택"])
    scene = None
    scene_label = ""

    if loc_mode == "📍 현재 위치(GPS)":
        try:
            from streamlit_geolocation import streamlit_geolocation
            gps = streamlit_geolocation()
            if gps and gps.get("latitude"):
                scene = (gps["longitude"], gps["latitude"])
                scene_label = f"현재 위치 ({gps['latitude']:.4f}, {gps['longitude']:.4f})"
                st.success("위치 확인됨")
            else:
                st.caption("위 아이콘을 눌러 위치 권한을 허용하세요.")
        except ModuleNotFoundError:
            st.warning("GPS 기능 설치 필요:\npip install streamlit-geolocation")

    elif loc_mode == "🔎 주소 검색":
        addr = st.text_input("주소/건물명", placeholder="예: 은평구 불광동 또는 은평구청")
        if addr:
            geo = geocode(addr)
            if geo:
                scene = geo
                scene_label = f"{addr} ({geo[1]:.4f}, {geo[0]:.4f})"
                st.success("주소 변환됨")
            else:
                st.error("주소를 찾지 못했습니다.")

    else:  # 동 선택
        dong = st.selectbox("현장 위치 (은평구)", list(DONG_COORDS.keys()))
        scene = DONG_COORDS[dong]
        scene_label = f"{dong}"

    st.divider()
    ptype_name = st.selectbox("환자 유형", list(PATIENT_TYPES.keys()))
    district_key = st.radio("검색 범위", list(DISTRICT_SETS.keys()), index=1)
    note = PATIENT_TYPES[ptype_name]["note"]
    if note:
        st.caption(f"ℹ️ {note}")
    go = st.button("이송 병원 추천", type="primary", use_container_width=True,
                   disabled=(scene is None))

districts = DISTRICT_SETS[district_key]
target_min = PATIENT_TYPES[ptype_name]["target_min"]

st.markdown("""
<style>
.suyong-hero{background:#FFFFFF;border:1px solid #FBE0D4;border-radius:16px;
  overflow:hidden;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,.04);}
.suyong-stripe{height:9px;background:repeating-linear-gradient(135deg,
  #E4002B 0 18px,#FFB400 18px 36px);}
.suyong-body{padding:22px 26px 20px;}
.suyong-eyebrow{font-size:12px;font-weight:800;letter-spacing:.14em;
  color:#E4002B;margin-bottom:10px;}
.suyong-h1{font-size:30px;line-height:1.2;font-weight:800;color:#16181D;margin:0 0 8px;}
.suyong-h1 b{color:#E4002B;}
.suyong-sub{font-size:15px;color:#4B4B4B;margin:0 0 16px;max-width:700px;}
.suyong-steps{display:flex;gap:10px;flex-wrap:wrap;}
.suyong-step{display:flex;align-items:center;gap:8px;background:#FFF6EC;
  border:1px solid #FFE2BC;border-radius:999px;padding:7px 14px;
  font-size:13px;font-weight:600;color:#7A4B00;}
.suyong-step .n{display:inline-flex;width:20px;height:20px;border-radius:50%;
  background:#E4002B;color:#fff;font-size:12px;font-weight:800;
  align-items:center;justify-content:center;}
</style>
<div class="suyong-hero">
  <div class="suyong-stripe"></div>
  <div class="suyong-body">
    <div class="suyong-eyebrow">🚑 수용ON · 119 응급이송 의사결정 지원</div>
    <div class="suyong-h1">전화 돌리지 마세요.<br><b>지금 받아주는 응급실</b>을 바로 찾아드립니다.</div>
    <div class="suyong-sub">현장 위치와 환자 상태만 입력하면, 수용 가능한 가장 빠른 응급실을 자동으로 추천합니다.</div>
    <div class="suyong-steps">
      <span class="suyong-step"><span class="n">1</span> 위치 입력</span>
      <span class="suyong-step"><span class="n">2</span> 환자 상태 선택</span>
      <span class="suyong-step"><span class="n">3</span> 받아주는 응급실 추천</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# 추천
# ──────────────────────────────────────────────
if go and scene:
    with st.spinner("실시간 병상·수용가능·경로 조회 중..."):
        try:
            top, allr = recommend(scene, ptype_name, districts)
        except Exception as e:
            st.error(f"조회 실패: {e}")
            st.stop()

    if not top:
        st.warning("조건(수용가능+장비)을 만족하는 병원이 없습니다. "
                   "검색 범위를 넓히거나 119 구급상황관리센터 의료지도를 요청하세요.")
        st.stop()

    head = f"📍 {scene_label} → {ptype_name}"
    if target_min:
        head += f"  ·  목표 {target_min}분 이내"
    st.subheader(head)

    msg_idx = load_diss_messages()   # {hpid: [수용불가 공지...]}

    cols = st.columns(len(top))
    medals = ["🥇 1순위", "🥈 2순위", "🥉 3순위"]
    for col, medal, r in zip(cols, medals, top):
        with col:
            st.markdown(f"### {medal}")
            st.markdown(f"**{r['name']}**")
            st.metric("예상 이송시간", f"{r['duration_min']}분", f"{r['distance_km']}km")
            if target_min is not None:
                if r["duration_min"] <= target_min:
                    st.markdown("🟢 **목표시간 내 도착 가능**")
                else:
                    st.markdown(f"🔴 **목표시간 초과 (+{r['duration_min'] - target_min:.0f}분)**")
            if r["accepted"]:
                st.markdown("✅ 수용가능: " + ", ".join(r["accepted"]))
            bed_txt = "정보없음" if r["er_beds"] is None else f"{r['er_beds']}석"
            if r["saturated"]:
                st.error(f"응급실 잔여 {bed_txt} — 포화")
            else:
                st.success(f"응급실 잔여 {bed_txt}")
            if r["mago"] is not None and r["mago"] > STALE_MIN:
                st.warning(f"⚠️ 병상정보 {r['ftxt']} 갱신 — 전화확인 권장")
            else:
                st.caption(f"🕐 병상정보 {r['ftxt']} 갱신")
            # 수집 데이터 기반 혼잡도 가중(있을 때만)
            if r.get("sat_rate") is not None and r.get("sat_pen"):
                st.caption(f"📊 이 시간대 포화율 {r['sat_rate']:.0%} → 혼잡도 가중 +{r['sat_pen']:.0f}분")
            # 응급실 수용불가/진료불가 공지 (있으면 경고만, 추천에서 제외하진 않음)
            for w in msg_idx.get(r["hpid"], []):
                if w.get("message"):
                    st.warning(f"🚨 수용불가 공지: {w['message']}")
            if r["tel_er"]:
                st.caption(f"☎ 응급실 직통 {r['tel_er']}")

    # 지도
    rows = [{"name": f"현장", "lat": scene[1], "lon": scene[0], "color": [230, 57, 70], "radius": 140}]
    for i, r in enumerate(top):
        rows.append({"name": f"{i+1}순위 {r['name']}", "lat": r["lat"], "lon": r["lon"],
                     "color": [42, 157, 143] if i == 0 else [29, 53, 87], "radius": 110})
    dfm = pd.DataFrame(rows)
    # 모든 핀이 한 화면에 들어오도록 위경도 범위로 줌 자동 계산
    lat_span = dfm["lat"].max() - dfm["lat"].min()
    lon_span = dfm["lon"].max() - dfm["lon"].min()
    span = max(lat_span, lon_span, 0.005)
    zoom = 14 if span < 0.01 else 13 if span < 0.03 else 12 if span < 0.06 else 11
    layers = [pdk.Layer("ScatterplotLayer", data=dfm, get_position="[lon, lat]",
                        get_fill_color="color", get_radius="radius", pickable=True)]
    # 1·2·3순위 경로선 (순위별 색상)
    route_colors = [[42, 157, 143], [69, 123, 157], [168, 196, 220]]  # 1=초록,2=청,3=연청
    route_widths = [6, 4, 3]
    for i, r in enumerate(top):
        if r.get("path"):
            layers.insert(0, pdk.Layer(
                "PathLayer",
                data=[{"path": r["path"]}],
                get_path="path", get_color=route_colors[i], get_width=route_widths[i],
                width_min_pixels=route_widths[i] - 1))
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=dfm["lat"].mean(),
                                         longitude=dfm["lon"].mean(), zoom=zoom),
        layers=layers,
        tooltip={"text": "{name}"}))
    st.caption("🔴 현장 · 🟢 1순위 · 🔵 2~3순위 (경로선 표시)")

    with st.expander(f"전체 후보 비교 ({len(allr)}곳)"):
        st.dataframe(pd.DataFrame([{
            "병원": r["name"], "예상이송(분)": r["duration_min"], "거리(km)": r["distance_km"],
            "응급실잔여": r["er_beds"], "상태": "포화" if r["saturated"] else "여유",
            "수용가능질환": ", ".join(r["accepted"]) or "-", "갱신": r["ftxt"],
            "공지": "🚨" if msg_idx.get(r["hpid"]) else "",
        } for r in allr]), use_container_width=True, hide_index=True)
else:
    st.info("왼쪽에서 현장 위치(GPS/주소/동)와 환자 유형을 고르고 **이송 병원 추천**을 눌러주세요.")

# ──────────────────────────────────────────────
# 구급 빅데이터 근거 패널 (소방청 구급활동 25.5만건 분석)
# ──────────────────────────────────────────────
st.divider()
with st.expander("📊 서비스 근거 데이터 — 왜 '수용ON'이 필요한가", expanded=False):
    st.caption("소방청 구급활동정보 분석 (2025년 상반기, 서울 25개 소방서 · 약 25.5만 건)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("골든타임 위험 출동", "21.9%", "5건 중 1건")
    m2.metric("심정지·호흡정지", "7,145건", "1분이 생존 좌우")
    m3.metric("60세 이상 고령", "41.8%", "10.7만 건")
    m4.metric("집에서 발생", "49.7%", "12.7만 건")
    st.caption(
        "분초를 다투는 응급환자가 전체의 1/5. 현재는 구급대원이 현장에서 병원마다 전화해 "
        "수용 가능 여부를 확인하느라 골든타임을 소모한다. '수용ON'은 이 과정을 "
        "**실시간 수용가능 병원 자동 추천**으로 대체한다."
    )

    st.markdown("**지금 서울 응급실 포화 현황**")
    try:
        seoul = get_er_beds("서울특별시")
        rows = [{"병원": h["name"], "잔여병상": h["er_beds"]}
                for h in seoul if h["er_beds"] is not None]
        df = pd.DataFrame(rows).sort_values("잔여병상")
        nsat = int((df["잔여병상"] <= 0).sum())
        c1, c2 = st.columns([1, 3])
        with c1:
            st.metric("응급의료기관", f"{len(df)}곳")
            st.metric("포화(잔여 0 이하)", f"{nsat}곳")
        with c2:
            st.bar_chart(df.set_index("병원")["잔여병상"], height=260)
        st.caption("음수 = 정원 초과 수용 중.")
    except Exception as e:
        st.caption(f"포화 현황 로드 실패: {e}")

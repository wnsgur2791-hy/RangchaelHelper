# -*- coding: utf-8 -*-
"""점수차 -> 예상 승률

승률 = 1 / (1 + e^-(a + k x 점수차))   (S자 곡선, 로지스틱)
- k(기울기): 점수차 1점이 승률을 얼마나 올리는지. 기본값 0.024 (2026-10-18까지 58경기로 맞춘 값)
- a(기준점): 점수차 0일 때의 승률 보정. 기록이 없으면 0 (= 50%)
기록이 쌓이면 실제 승패로 a, k를 다시 맞춥니다. 기울기 k는 기록이 적을 때 기본값 쪽으로 끌어당겨서(사전값)
몇 경기 결과에 크게 흔들리지 않게 합니다.
기준점 a는 2026-10-18부터 거의 고정하지 않음(승률_기준점_불확실성 = 2.0): 98경기 기록에서 점수차 0일 때
실제 승률이 50%보다 높게(약 61%) 나와서, 50%에 묶어 두면 예상 승률이 실제보다 낮게 나왔기 때문.
"""
import math, os

import storage as ST
import score as SC

_CACHE = {'key': None, 'model': None}


def _settings():
    s = SC.SET
    return (float(s.get('승률_기울기', 0.024)), float(s.get('승률_기울기_불확실성', 0.012)),
            bool(s.get('승률_자동보정', True)), float(s.get('승리_승점', 7)), float(s.get('무승부_승점', 2)),
            float(s.get('무승부_확률', 0.03)), float(s.get('승률_기준점_불확실성', 2.0)))


def fit(rows, k0=0.024, k_sd=0.012, a_sd=0.4):
    """rows: [(점수차, 결과)] -> (a, k). 사전값 k~N(k0, k_sd), a~N(0, a_sd) 를 둔 로지스틱 회귀"""
    data = [(d, 1.0 if r == '승' else (0.5 if r == '무' else 0.0)) for d, r in rows if d is not None and r in ('승', '패', '무')]
    a, k = 0.0, k0
    for _ in range(50):
        ga = -a / a_sd ** 2
        gk = -(k - k0) / k_sd ** 2
        haa = -1 / a_sd ** 2
        hkk = -1 / k_sd ** 2
        hak = 0.0
        for x, y in data:
            p = 1 / (1 + math.exp(-(a + k * x)))
            w = p * (1 - p)
            ga += y - p
            gk += (y - p) * x
            haa -= w
            hkk -= w * x * x
            hak -= w * x
        det = haa * hkk - hak * hak
        if abs(det) < 1e-12:
            break
        da = (hkk * ga - hak * gk) / det
        dk = (haa * gk - hak * ga) / det
        a, k = a - da, k - dk
        if abs(da) < 1e-7 and abs(dk) < 1e-9:
            break
    return a, max(0.002, min(0.1, k)), len(data)


def model():
    """{'a', 'k', 'n', 'win_pts', 'draw_pts', 'draw_p', 'auto'}"""
    k0, k_sd, auto, win_pts, draw_pts, draw_p, a_sd = _settings()
    try:
        mt = os.path.getmtime(ST.MATCHES)
    except OSError:
        mt = None
    key = (mt, k0, k_sd, auto, a_sd)
    if _CACHE['key'] == key:
        return _CACHE['model']
    a, k, n = 0.0, k0, 0
    if auto and mt is not None:
        rows = [(r.get('점수차'), r.get('결과')) for r in ST.read_jsonl(ST.MATCHES) if r.get('분석제외') != 'Y']
        a, k, n = fit(rows, k0, k_sd, a_sd)
    m = {'a': a, 'k': k, 'n': n, 'win_pts': win_pts, 'draw_pts': draw_pts, 'draw_p': draw_p, 'auto': auto}
    _CACHE.update(key=key, model=m)
    return m


def p_win(diff, m=None):
    m = m or model()
    return 1 / (1 + math.exp(-(m['a'] + m['k'] * diff)))


def exp_points(diff, m=None):
    """기대 승점 = 승리 승점 x 승률 + 무승부 승점 x 무승부 확률 (무승부 확률은 고정값, 승률에서 일부를 떼어 냄)"""
    m = m or model()
    p = p_win(diff, m)
    d = m['draw_p'] * 4 * p * (1 - p)          # 팽팽할수록 무승부가 조금 더 나옴 (50%일 때 = draw_p)
    return m['win_pts'] * (p - d / 2) + m['draw_pts'] * d

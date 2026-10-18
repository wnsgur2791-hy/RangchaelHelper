# -*- coding: utf-8 -*-
"""보정점수 / 매치업 점수 계산.

기본 원리
- 선수 보정점수 = 능력치 가중합 + 잠재 점수 + 발사각 보너스 + 스킬 점수
- 스킬 점수는 갤러리 스킬점수표(랭킹챌린지 한 시즌 평균 기준)를 쓰되,
  1:1 상대·좌우·홈/원정·OVR·타순에 따라 달라지는 스킬은 '실제 이 경기의 조건'으로 다시 계산합니다.
  (점수표가 쓴 배율(0.8, 0.9, 1.1 등)은 그대로 유지하고 발동 확률만 바꿉니다.)
- 선발이 던지는 7이닝(7/9)은 실제 상대, 나머지 2이닝(2/9)은 상대 불펜을 모르니 점수표 평균값을 씁니다.
"""
import csv, os, re, json, math, itertools

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)

W_PJSI = 1 + 0.85 + 0.4 + 0.15     # 파정선인 = 2.4
W_PJ = 1 + 0.85                    # 파정 = 1.85
W_PJI = 1 + 0.85 + 0.15            # 파정인 = 2.0
W_PJS = 1 + 0.85 + 0.4             # 파정선 = 2.25
W_GB = 1.35 + 1.05                 # 구위+변화 = 2.4 (구속/제구는 0)
NERF = 1.1                         # 상대 너프 배율
P_COND = 0.8                       # 투수 조건부 배율


def load_settings():
    with open(os.path.join(APP_DIR, 'settings_default.json'), encoding='utf-8') as f:
        s = json.load(f)
    user = os.path.join(ROOT_DIR, '설정.json')
    if os.path.isfile(user):
        with open(user, encoding='utf-8') as f:
            s.update(json.load(f))
    return s


SET = load_settings()


# ============================================================ 표 불러오기
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_skills():
    tbl = {'타자': {}, '투수': {}}
    with open(os.path.join(APP_DIR, 'data', 'skills.csv'), encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            vals = [_num(row[f'Lv{i}']) for i in range(5, 11)]
            tbl[row['구분']].setdefault(row['스킬명'], vals)
    return tbl


def load_potential():
    t = {}
    with open(os.path.join(APP_DIR, 'data', 'potential.csv'), encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            t[row['등급']] = {k: float(v) for k, v in row.items() if k != '등급'}
    return t


SKILLS = load_skills()
POT = load_potential()


def base_name(n):
    return re.sub(r'\(.*?\)', '', n or '').replace(' ', '')


def skill_names(role):
    return sorted({base_name(n) for n in SKILLS[role]})


def table(role, name, lv):
    """점수표 값 (없는 레벨이면 가장 가까운 아래 레벨)"""
    vals = SKILLS[role].get(name)
    if not vals or lv is None:
        return None
    i = max(0, min(5, int(lv) - 5))
    while i >= 0 and vals[i] is None:
        i -= 1
    return vals[i] if i >= 0 else None


def variants(role, base):
    return [n for n in SKILLS[role] if base_name(n) == base]


def lvl_arr(arr, lv):
    return arr[max(0, min(5, int(lv) - 5))]


# ============================================================ 조건 계산 도우미
def pa_weights():
    w = SET['타순별_타석수']
    return [x / sum(w) for x in w]


def p_rhp(opp_sp_hand):
    """타자가 우투수를 상대할 확률 (선발 7/9은 실제, 불펜 2/9은 평균)"""
    if opp_sp_hand not in ('L', 'R'):          # 상대를 모를 때(평균 조건)
        return SET['평균_우투_비율']
    s = SET['선발_담당_비율']
    real = 1.0 if opp_sp_hand == 'R' else 0.0
    return s * real + (1 - s) * SET['평균_우투_비율']


def mix(real, avg):
    s = SET['선발_담당_비율']
    return s * real + (1 - s) * avg


def nanse_on(my_ovr, opp_ovr):
    if my_ovr is None or opp_ovr is None:
        return None
    return 1.0 if my_ovr < opp_ovr else 0.0


def batter_side_shares(lineup, pitcher_hand):
    """투수 입장에서 상대 타선의 좌타석/우타석 비율 (타석 수 가중, 양타는 투수 반대 타석)"""
    w = pa_weights()
    left = right = 0.0
    for b in lineup:
        k = (b.get('slot') or 5) - 1
        hand = b.get('bat') or 'R'
        if hand == 'S':
            hand = 'L' if pitcher_hand == 'R' else 'R'
        if hand == 'L':
            left += w[k]
        else:
            right += w[k]
    tot = left + right or 1
    return left / tot, right / tot


# 점수표가 가정한 평균 비율
AVG_L, AVG_R, AVG_S = SET['평균_좌타_비율'], 1 - SET['평균_좌타_비율'] - SET['평균_양타_비율'], SET['평균_양타_비율']


def avg_share(target, pitcher_hand):
    """점수표 기준 평균: 투수(손)가 상대하는 좌타석/우타석 비율"""
    if target == 'L':
        return AVG_L + (AVG_S if pitcher_hand == 'R' else 0)
    return AVG_R + (AVG_S if pitcher_hand == 'L' else 0)


# ============================================================ 잠재/기본 점수
def potential_points(role, grades):
    g = list(grades or []) + ['-'] * 6
    def p(grade, col):
        return POT.get(grade or '-', POT['-']).get(col, 0.0)
    if role == '타자':
        return p(g[0], '풀스윙') + p(g[1], '클러치') + p(g[5], '각성잠재')
    return p(g[0], '장타억제력') + p(g[1], '침착성') + p(g[5], '각성잠재')


def angle_bonus(power, angle):
    """관리기 엑셀 규칙: 발사각 13도 이상이면 필요 파워 = |16-발사각|x3+160, 넘으면 최대 5점"""
    if angle is None or power is None or angle < 13:
        return 0.0
    need = abs(16 - angle) * 3 + 160
    if power < need:
        return 0.0
    return min(5.0, angle - 12 if angle <= 16 else 4 + (angle - 16) * 0.5)


def batter_base(p):
    w = SET['타자_가중치']
    stat = sum(w[k] * (p.get(k) or 0) for k in ('파워', '정확', '선구', '인내'))
    pen = SET['좌타_불펜감점'] * (1 - SET['선발_담당_비율']) if p.get('bat') == 'L' else 0.0
    return stat, potential_points('타자', p.get('grades')), angle_bonus(p.get('파워'), p.get('발사각')), -pen


def pitcher_base(p):
    w = SET['투수_가중치']
    stat = w['구위'] * (p.get('구위') or 0) + w['변화'] * (p.get('변화') or 0)
    lefty = SET['좌완_보너스'] if p.get('throw') == 'L' else 0.0
    return stat, potential_points('투수', p.get('grades')), lefty


# ============================================================ 타자 스킬
def batter_skill(name, lv, p, c, notes):
    """c: 매치업 조건
       opp_sp_hand, opp_sp_ovr, opp_sp_stars, opp_catcher_def, home(bool), my_team_ovr, opp_team_ovr"""
    R = '타자'
    b = base_name(name)
    slot = p.get('slot')
    pr = p_rhp(c.get('opp_sp_hand'))

    if b in ('우완킬러', '좌완킬러'):
        t = table(R, b, lv)
        share = pr if b == '우완킬러' else 1 - pr
        base = SET['평균_우투_비율'] if b == '우완킬러' else 1 - SET['평균_우투_비율']
        return t * share / base
    if b == '저니맨':
        t = table(R, b, lv)
        nerf = lvl_arr([2, 3, 4, 5, 6, 7], lv) * W_GB * NERF
        return t if not c.get('home') else t - nerf
    if b == '홈어드밴티지':
        t = table(R, b, lv)
        return t + (1 * W_PJI if c.get('home') else 0)
    if b == '난세의영웅':
        t = table(R, b, lv)
        unc = lvl_arr([1, 2, 2, 3, 3, 4], lv) * W_PJSI
        on = nanse_on(c.get('my_team_ovr'), c.get('opp_team_ovr'))
        if on is None:
            return t
        return unc + (t - unc) * mix(on, 0.4) / 0.4
    if b == '좌타해결사':
        a = lvl_arr([5, 5, 6, 6, 7, 7], lv)
        nb = lvl_arr([3, 4, 4, 5, 5, 6], lv)
        hand = p.get('bat')
        p_left = 1.0 if hand == 'L' else (pr if hand == 'S' else 0.0)
        return a * W_PJ + 2 * W_PJ * pr + nb * W_GB * NERF * p_left
    if b == '스위치히터':
        a = lvl_arr([3, 3, 4, 4, 5, 5], lv)
        nb = lvl_arr([5, 6, 8, 10, 12, 14], lv)
        cc = lvl_arr([4, 5, 5, 6, 6, 7], lv)
        hand = p.get('bat')
        opp = 1.0 if hand == 'S' else (pr if hand == 'L' else 1 - pr)
        return a * W_PJ + nb * 1.35 * NERF * opp + (cc * W_PJSI if hand == 'S' else 0)
    if b == '선봉장':
        a = lvl_arr([3, 3, 4, 4, 5, 5], lv)
        bb = lvl_arr([3, 4, 6, 8, 10, 12], lv)
        cc = lvl_arr([3, 4, 5, 6, 7, 8], lv)
        top = 1 if slot in (1, 2) else 0
        cd = c.get('opp_catcher_def')
        run = p.get('주루')
        if cd is None or run is None:
            fast = 0.8 if (run or 0) >= 142 else 0.5 if (run or 0) >= 130 else 0.2
        else:
            fast = 1.0 if run > cd else 0.0
        return a * W_PJ * top + bb * W_GB * NERF * 0.578 + cc * W_GB * NERF * 0.821 * fast
    if b == '5툴플레이어':
        pct = lvl_arr([0.02, 0.03, 0.03, 0.04, 0.04, 0.05], lv)
        return math.floor(((p.get('주루') or 0) + (p.get('수비') or 0)) * pct) * W_PJ
    if b == '패기':
        n = int(lv)
        if n > 8:
            n = 8
        ct = p.get('card_type') or '시그'
        bull = SET['패기_불펜발동확률'].get(ct, 0.2)
        mine, opp = p.get('ovr'), c.get('opp_sp_ovr')
        if mine is None or opp is None:           # 평균 조건: 점수표 그대로
            nm = {'임팩': '패기(임팩)', '국대': '패기(국대)', '골글': '패기(골글)'}.get(ct, '패기(시그/올스타)')
            return table(R, nm, n)
        real = 1.0 if opp > mine else 0.0
        return (n + 2) * W_PJSI * mix(real, bull)
    if b == '도전정신':
        n = int(lv)
        my_st = p.get('stars') or 5
        bull = SET['도전정신_4성_불펜발동확률'] if my_st <= 4 else 0.0
        if c.get('opp_sp_stars') is None:
            real = bull
        else:
            real = 1.0 if c.get('opp_sp_stars') > my_st else 0.0
        return n * W_PJSI + 1 * W_PJSI * NERF * mix(real, bull)
    # ---- 타순 스킬
    top = slot in (1, 2)
    low = slot in (6, 7, 8, 9)
    mid = slot in (3, 4, 5)
    if b == '리드오프':
        return table(R, '리드오프(타순O)' if top else '리드오프(타순X)', lv)
    if b == '컨택트히터':
        return table(R, '컨택트히터(타순배치)' if top else '컨택트히터(타순X)', lv)
    if b == '빈틈없는타선':
        return table(R, '빈틈없는타선(타순O)' if low else '빈틈없는타선(타순X)', lv)
    if b == '공포의하위타선':
        return table(R, '공포의하위타선(타순O)' if low else '공포의하위타선(타순X)', lv)
    if b == '핵타선':
        return table(R, '핵타선(타순O)', lv) if mid else 0.0
    if b == '수비안정성':
        return table(R, '수비안정성(타순O)', lv) if (top or low) else 0.0
    if b == '포수리드':
        nm = '포수리드(버프포함)' if p.get('pos') == 'C' else '포수리드'
        return table(R, nm, lv)
    # ---- 그 외: 점수표 그대로
    vs = variants(R, b)
    if not vs:
        notes.append(f'점수표에 없는 스킬: {name}')
        return 0.0
    return table(R, vs[0], lv) or 0.0


# ============================================================ 투수 스킬
STAMINA_ROWS = [(140, 149, '철완(140149)'), (134, 139, '철완(134139)'), (120, 133, '철완(120133)'),
                (117, 119, '철완(117119)'), (0, 116, '철완(100116)')]


def pitcher_skill(name, lv, p, c, notes):
    """c: lineup(상대 타자 목록: bat, ovr, stars, slot), home, my_team_ovr, opp_team_ovr"""
    R = '투수'
    b = base_name(name)
    hand = p.get('throw') or 'R'
    lineup = c.get('lineup') or []
    # 선발투수는 상대 선발 타선만 상대하므로 실제 비율 그대로 사용 (상대를 모르면 점수표 평균)
    sl, sr = batter_side_shares(lineup, hand) if lineup else (avg_share('L', hand), avg_share('R', hand))
    w = pa_weights()

    if b in ('우타킬러', '좌타킬러'):
        nm = f"{b}({'좌투' if hand == 'L' else '우투'})"
        t = table(R, nm, lv)
        tgt = 'R' if b == '우타킬러' else 'L'
        share = sr if tgt == 'R' else sl
        return t * share / avg_share(tgt, hand)
    if b == '좌승사자':
        n = int(lv)
        a = lvl_arr([4, 5, 5, 5, 5, 5], lv)
        bb = lvl_arr([5, 6, 8, 10, 12, 14], lv)
        return (n - 2) * W_GB + a * W_PJS * NERF * sl * P_COND + (bb * W_PJS * NERF if hand == 'L' else 0)
    if b == '저니맨':
        t = table(R, b, lv)
        nerf = lvl_arr([2, 3, 4, 5, 6, 7], lv) * W_PJ * NERF
        return t if not c.get('home') else t - nerf
    if b == '홈어드밴티지':
        t = table(R, b, lv)
        return t + (1 * W_GB if c.get('home') else 0)
    if b == '난세의영웅':
        t = table(R, '난세의영웅(선발)', lv)
        unc = lvl_arr([1, 2, 2, 3, 3, 4], lv) * W_GB
        on = nanse_on(c.get('my_team_ovr'), c.get('opp_team_ovr'))
        if on is None:
            return t
        return unc + (t - unc) * on / 0.6       # 선발은 경기 대부분을 던지므로 실제 발동 여부만 반영
    if b == '패기':
        ct = p.get('card_type') or '시그'
        if ct == '임팩':
            nm, pt = '패기(임팩선발)', 0.95
        elif ct == '골글':
            nm, pt = '패기(골글)', 0.6
        else:
            nm, pt = '패기(시그/올스타선발)', 0.8
        t = table(R, nm, lv)
        mine = p.get('ovr')
        if not lineup or mine is None:
            return t
        act = sum(w[(x.get('slot') or 5) - 1] for x in lineup if (x.get('ovr') or 0) > mine)
        return t * act / pt
    if b == '도전정신':
        n = int(lv)
        my_st = p.get('stars') or 5
        if not lineup:
            act = 0.85 if my_st <= 4 else 0.0
        else:
            act = sum(w[(x.get('slot') or 5) - 1] for x in lineup if (x.get('stars') or 5) > my_st)
        return n * W_GB + 1 * W_GB * NERF * act * P_COND
    if b == '원투펀치':
        return table(R, '원투펀치(배치O)', lv)
    if b == '라이징스타':
        rot = p.get('rotation')
        return table(R, '라이징스타(3~5선발)' if rot in (3, 4, 5) else '라이징스타(배치X)', lv)
    if b == '철완':
        st = min(149, p.get('지구력') or 0)
        for lo, hi, nm in STAMINA_ROWS:
            if lo <= st <= hi:
                return table(R, nm, lv)
        return table(R, '철완(100116)', lv)
    # ---- 그 외: 선발용 행이 있으면 선발용, 없으면 첫 행
    vs = variants(R, b)
    if not vs:
        notes.append(f'점수표에 없는 스킬: {name}')
        return 0.0
    pick = next((v for v in vs if '선발' in v and '3~5' not in v), vs[0])
    return table(R, pick, lv) or 0.0


# ============================================================ 선수 점수
def batter_score(p, c):
    notes = []
    stat, pot, ang, pen = batter_base(p)
    sk = []
    for nm, lv in p.get('skills') or []:
        if not nm or lv is None:
            continue
        v = batter_skill(nm, lv, p, c, notes)
        sk.append((nm, lv, round(v or 0, 2)))
    skill = sum(x[2] for x in sk)
    return {'total': stat + pot + ang + pen + skill, 'stat': stat, 'pot': pot, 'angle': ang,
            'pen': pen, 'skill': skill, 'skills': sk, 'notes': notes}


def pitcher_score(p, c):
    notes = []
    stat, pot, lefty = pitcher_base(p)
    sk = []
    for nm, lv in p.get('skills') or []:
        if not nm or lv is None:
            continue
        v = pitcher_skill(nm, lv, p, c, notes)
        sk.append((nm, lv, round(v or 0, 2)))
    skill = sum(x[2] for x in sk)
    return {'total': stat + pot + lefty + skill, 'stat': stat, 'pot': pot, 'lefty': lefty,
            'skill': skill, 'skills': sk, 'notes': notes}


# ============================================================ 팀 / 매치업
def team_ovr(batters, sp):
    vals = [b.get('ovr') for b in batters] + [sp.get('ovr')]
    if any(v is None for v in vals):
        return None
    return sum(vals) / len(vals)


def catcher_def(batters):
    for b in batters:
        if b.get('pos') == 'C':
            return b.get('수비')
    return None


def lineup_score(batters, opp_sp, home, my_tovr, opp_tovr, opp_batters):
    w = pa_weights()
    c = {'opp_sp_hand': opp_sp.get('throw'), 'opp_sp_ovr': opp_sp.get('ovr'),
         'opp_sp_stars': opp_sp.get('stars'), 'opp_catcher_def': catcher_def(opp_batters),
         'home': home, 'my_team_ovr': my_tovr, 'opp_team_ovr': opp_tovr}
    tot = 0.0
    det = []
    for b in batters:
        s = batter_score(b, c)
        k = (b.get('slot') or 5) - 1
        tot += w[k] * s['total']
        det.append(s)
    return tot, det       # 타석수 가중 평균


def matchup(my_bat, my_sp, opp_bat, opp_sp):
    """한 경기 (내 선발 my_sp vs 상대 선발 opp_sp)"""
    my_t = team_ovr(my_bat, my_sp)
    op_t = team_ovr(opp_bat, opp_sp)
    my_l, my_ld = lineup_score(my_bat, opp_sp, False, my_t, op_t, opp_bat)
    op_l, op_ld = lineup_score(opp_bat, my_sp, True, op_t, my_t, my_bat)
    my_p = pitcher_score(my_sp, {'lineup': opp_bat, 'home': False, 'my_team_ovr': my_t, 'opp_team_ovr': op_t})
    op_p = pitcher_score(opp_sp, {'lineup': my_bat, 'home': True, 'my_team_ovr': op_t, 'opp_team_ovr': my_t})
    mine = my_l + my_p['total']
    theirs = op_l + op_p['total']
    return {'my_lineup': my_l, 'my_sp': my_p['total'], 'opp_lineup': op_l, 'opp_sp': op_p['total'],
            'my_total': mine, 'opp_total': theirs, 'diff': mine - theirs,
            'my_team_ovr': my_t, 'opp_team_ovr': op_t,
            'my_sp_detail': my_p, 'opp_sp_detail': op_p, 'my_lineup_detail': my_ld, 'opp_lineup_detail': op_ld}


def best_assignment(my_bat, my_sps, opps, value=None):
    """opps: [(상대 타자 9명, 상대 선발)] x5. 120가지 배치를 전부 계산해서 value(점수차)의 합이 가장 큰 배치를 찾음.
    value를 안 주면 점수차 합, 기대 승점 함수를 주면 기대 승점 합이 기준"""
    n = len(opps)
    M = [[matchup(my_bat, my_sps[i], ob, osp) for (ob, osp) in opps] for i in range(len(my_sps))]
    f = value or (lambda d: d)
    V = [[f(M[i][j]['diff']) for j in range(n)] for i in range(len(my_sps))]
    best = None
    for perm in itertools.permutations(range(len(my_sps)), n):
        s = sum(V[perm[j]][j] for j in range(n))
        if best is None or s > best[0] + 1e-9:
            best = (s, perm)
    return M, best

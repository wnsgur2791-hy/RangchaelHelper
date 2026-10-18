# -*- coding: utf-8 -*-
"""엑셀에서 직접 고친 내용을 원본 기록(기록데이터 폴더)에 반영

- 매치기록 / 선수기록 / 내 덱 시트를 읽어서, 원본과 다른 칸만 찾아 바꿉니다.
- 선수 값(스탯, 스킬, 잠재, 좌우 등)이 바뀐 경기는 점수와 점수차를 다시 계산합니다.
- 바꾸기 전에 원본을 기록데이터\\백업_날짜시각 폴더에 복사해 둡니다.
"""
import os, re, shutil, tempfile
from openpyxl import load_workbook

import storage as ST
import score as SC
import recognize as R

# 사람이 고칠 수 있는 칸 (나머지 점수 칸은 다시 계산)
MATCH_EDIT = ['상대닉네임', '상대POINT', '상대OVR(로비)', '상대타자OVR', '상대선발', '상대선발손', '상대선발순번',
              '내선발', '내선발손', '내선발순번', '결과', '스코어', '내득점', '상대득점', '분석제외']
PLAYER_EDIT = ['이름', '포지션', 'OVR', '별', '카드종류', '투', '타', '파워/구속', '정확/변화', '선구/구위', '인내/제구',
               '주루/지구력', '수비', '발사각', '잠재1', '잠재2', '잠재3', '잠재4', '잠재5', '각잠',
               '스킬1', 'Lv1', '스킬2', 'Lv2', '스킬3', 'Lv3', '타순/순번']
PLAYER_NAME_ONLY = {'이름', '포지션'}          # 점수에 영향 없는 칸
STAT_COLS = ['파워/구속', '정확/변화', '선구/구위', '인내/제구', '주루/지구력', '수비']
INT_COLS = {'상대POINT', '상대선발순번', '내선발순번', '내득점', '상대득점', 'OVR', '별', 'Lv1', 'Lv2', 'Lv3',
            '타순/순번', '발사각', '타순'} | set(STAT_COLS)
FLOAT_COLS = {'상대OVR(로비)', '상대타자OVR'}
UPPER_COLS = {'투', '타', '포지션', '잠재1', '잠재2', '잠재3', '잠재4', '잠재5', '각잠', '상대선발손', '내선발손'}


def norm(col, v):
    """엑셀 칸 값과 원본 값을 같은 모양으로 맞춤 (171.0 -> 171, ' sr+ ' -> 'SR+', 빈칸 -> None)"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == '':
            return None
    if col in INT_COLS:
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return v
    if col in FLOAT_COLS:
        try:
            return round(float(v), 1)
        except (TypeError, ValueError):
            return v
    if col in UPPER_COLS and isinstance(v, str):
        return v.upper()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def read_sheet(wb, title):
    if title not in wb.sheetnames:
        return None
    ws = wb[title]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(h).strip() if h is not None else '' for h in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(v is None or (isinstance(v, str) and not v.strip()) for v in r):
            continue
        out.append({h: v for h, v in zip(head, r) if h})
    return out


def _key_m(r):
    return (str(r.get('기록시각') or '').strip(), norm('줄', r.get('줄')))


def _key_p(r):
    return (str(r.get('기록시각') or '').strip(), norm('줄', r.get('줄')), str(r.get('팀') or '').strip(),
            str(r.get('역할') or '').strip())


# ------------------------------------------------------------ 선수 행 <-> 선수 정보
def row_to_player(r, sn):
    pit = str(r.get('역할')) == '선발'
    st = [norm(c, r.get(c)) for c in STAT_COLS]
    keys = ['구속', '변화', '구위', '제구', '지구력', '수비'] if pit else ['파워', '정확', '선구', '인내', '주루', '수비']
    p = {'name': r.get('이름'), 'pos': r.get('포지션'), 'pitcher': pit, 'ovr': norm('OVR', r.get('OVR')),
         'stars': norm('별', r.get('별')), 'card_type': r.get('카드종류'),
         'throw': norm('투', r.get('투')), 'bat': norm('타', r.get('타'))}
    for k, v in zip(keys, st):
        p[k] = v
    if not pit:
        p['발사각'] = norm('발사각', r.get('발사각'))
        p['slot'] = norm('타순/순번', r.get('타순/순번'))
    else:
        p['rotation'] = norm('타순/순번', r.get('타순/순번'))
    p['grades'] = [norm('잠재1', r.get(c)) or '-' for c in ('잠재1', '잠재2', '잠재3', '잠재4', '잠재5', '각잠')]
    sk = []
    for i in (1, 2, 3):
        nm = r.get(f'스킬{i}')
        nm = str(nm).strip() if nm is not None else ''
        if not nm:
            continue
        m, s = sn.match(nm, pit)
        sk.append((m if (m and s >= 0.8) else nm, norm('Lv1', r.get(f'Lv{i}'))))
    p['skills'] = sk
    return p


def fix_ovr(p, before_stats):
    """스탯을 고쳤는데 OVR은 그대로면 OVR을 스탯 평균으로 다시 계산"""
    keys = ['구속', '변화', '구위', '제구', '지구력', '수비'] if p['pitcher'] else ['파워', '정확', '선구', '인내', '주루', '수비']
    vals = [p.get(k) for k in keys]
    if all(isinstance(v, int) for v in vals) and vals != before_stats:
        p['ovr'] = sum(vals) // 6


# ------------------------------------------------------------ 반영
def apply_excel(path, log=print, recalc_all=False):
    """path의 엑셀 내용을 원본 기록에 반영. 결과 요약 dict
    recalc_all=True 면 고치지 않은 경기도 지금 점수표/설정으로 점수를 전부 다시 계산"""
    tmp = os.path.join(tempfile.gettempdir(), 'rankchal_import.xlsx')
    try:
        shutil.copy(path, tmp)                      # 엑셀이 열려 있어도 읽을 수 있게 복사본을 읽음
        wb = load_workbook(tmp, data_only=True)
    except PermissionError:
        raise RuntimeError('엑셀 파일을 읽지 못했어요. 엑셀에서 [저장]한 뒤 엑셀을 닫고 다시 눌러 주세요.')
    sn = R.SkillNames(SC.skill_names('타자'), SC.skill_names('투수'))
    matches = ST.read_jsonl(ST.MATCHES)
    players = ST.read_jsonl(ST.PLAYERS)
    deck = ST.load_json(ST.MY_DECK)
    summary = {'match_cells': 0, 'player_cells': 0, 'recalc': 0, 'deck_cells': 0, 'notes': []}

    # ---------- 매치기록
    xm = read_sheet(wb, '매치기록')
    if xm is not None:
        by = {}
        for r in xm:
            by.setdefault(_key_m(r), r)
        for m in matches:
            r = by.get(_key_m(m))
            if r is None:
                continue
            for c in MATCH_EDIT:
                if c not in r:
                    continue
                new, old = norm(c, r.get(c)), norm(c, m.get(c))
                if new != old:
                    m[c] = new
                    summary['match_cells'] += 1
            # 스코어를 고쳤으면 득점 칸도 맞춤
            sc = str(m.get('스코어') or '')
            mm = re.match(r'^\s*(\d+)\s*[:\-]\s*(\d+)\s*$', sc)
            if mm:
                m['스코어'] = f'{mm.group(1)}:{mm.group(2)}'
                m['내득점'], m['상대득점'] = int(mm.group(1)), int(mm.group(2))
                a_, b_ = m['내득점'], m['상대득점']
                res = '승' if a_ > b_ else ('패' if a_ < b_ else '무')
                if m.get('결과') != res:                 # 스코어와 결과가 다르면 스코어를 믿음
                    summary['notes'].append(f"{m.get('기록시각')} 줄{m.get('줄')}: 결과 '{m.get('결과')}' → 스코어 {m['스코어']}에 맞춰 '{res}'(으)로 고침")
                    m['결과'] = res
                    summary['match_cells'] += 1
            if m.get('결과') not in (None, '승', '패', '무'):
                summary['notes'].append(f"{m.get('기록시각')} 줄{m.get('줄')}: 결과 '{m.get('결과')}' → 승/패/무 중 하나로 적어 주세요")

    # ---------- 선수기록
    changed_games = set()
    xp = read_sheet(wb, '선수기록')
    if xp is not None:
        groups = {}
        for r in xp:
            groups.setdefault(_key_p(r), []).append(r)
        seen = {}
        for p in players:
            k = _key_p(p)
            i = seen.get(k, 0)
            seen[k] = i + 1
            lst = groups.get(k) or []
            # 같은 경기·팀·역할 안에서 타순(선발은 1명)이 같은 행을 찾고, 겹치면 이름까지, 그래도 안 되면 순서대로
            slot = norm('타순/순번', p.get('타순/순번'))
            same = [x for x in lst if norm('타순/순번', x.get('타순/순번')) == slot] if p.get('역할') == '타자' else lst
            if len(same) > 1:
                same = [x for x in same if norm('이름', x.get('이름')) == norm('이름', p.get('이름'))] or same[:0]
            r = same[0] if len(same) == 1 else (lst[i] if i < len(lst) else None)
            if r is None:
                continue
            before = [norm(c, p.get(c)) for c in STAT_COLS]
            touched = False
            for c in PLAYER_EDIT:
                if c not in r:
                    continue
                new, old = norm(c, r.get(c)), norm(c, p.get(c))
                if new != old:
                    p[c] = new
                    summary['player_cells'] += 1
                    if c not in PLAYER_NAME_ONLY:
                        touched = True
            if touched:
                after = [norm(c, p.get(c)) for c in STAT_COLS]
                if after != before and all(isinstance(v, int) for v in after) and norm('OVR', r.get('OVR')) == norm('OVR', p.get('OVR')):
                    p['OVR'] = sum(after) // 6
                changed_games.add((p['기록시각'], norm('줄', p.get('줄'))))
        # 선발 이름을 고쳤으면 매치기록 이름도 맞춤
        for p in players:
            if p.get('역할') != '선발':
                continue
            for m in matches:
                if (m.get('기록시각'), norm('줄', m.get('줄'))) == (p.get('기록시각'), norm('줄', p.get('줄'))):
                    col = '내선발' if p.get('팀') == '나' else '상대선발'
                    if m.get(col) != p.get('이름') and p.get('이름'):
                        m[col] = p.get('이름')

    # ---------- 상대 타자 목록이 잘못 읽힌 경기 찾기 (투수가 타자 자리에 있거나 같은 선수가 두 번)
    for m in matches:
        key = (m.get('기록시각'), norm('줄', m.get('줄')))
        rows = [p for p in players if (p.get('기록시각'), norm('줄', p.get('줄'))) == key and p.get('팀') == '상대']
        bats = [p for p in rows if p.get('역할') == '타자']
        sp = [p.get('이름') for p in rows if p.get('역할') == '선발']
        names = [p.get('이름') for p in bats]
        bad = any(str(p.get('포지션')) in ('SP', 'RP', 'CP') for p in bats) or any(n in sp for n in names) \
            or len(set(names)) < len(names)
        if bad and m.get('분석제외') != 'Y':
            m['분석제외'] = 'Y'
            summary['match_cells'] += 1
            summary['notes'].append(f"{m.get('기록시각')} 줄{m.get('줄')}: 상대 타자 한 명을 건너뛰고 읽은 경기라 승률 분석에서 뺐어요 (분석제외=Y)")

    # ---------- 점수 다시 계산
    if recalc_all:
        changed_games |= {(m.get('기록시각'), norm('줄', m.get('줄'))) for m in matches if m.get('분석제외') != 'Y'}
    for key in changed_games:
        rows = [p for p in players if (p.get('기록시각'), norm('줄', p.get('줄'))) == key]
        mine = [p for p in rows if p.get('팀') == '나']
        theirs = [p for p in rows if p.get('팀') == '상대']
        msp = [row_to_player(p, sn) for p in mine if p.get('역할') == '선발']
        osp = [row_to_player(p, sn) for p in theirs if p.get('역할') == '선발']
        mb = [row_to_player(p, sn) for p in mine if p.get('역할') == '타자']
        ob = [row_to_player(p, sn) for p in theirs if p.get('역할') == '타자']
        if not (msp and osp and len(mb) == 9 and len(ob) == 9):
            summary['notes'].append(f'{key[0]} 줄{key[1]}: 선수 행이 모자라서 점수를 다시 계산하지 못했어요')
            continue
        res = SC.matchup(mb, msp[0], ob, osp[0])
        for m in matches:
            if (m.get('기록시각'), norm('줄', m.get('줄'))) == key:
                m.update({'내타선점수': round(res['my_lineup'], 2), '내선발점수': round(res['my_sp'], 2),
                          '상대타선점수': round(res['opp_lineup'], 2), '상대선발점수': round(res['opp_sp'], 2),
                          '내합계': round(res['my_total'], 2), '상대합계': round(res['opp_total'], 2),
                          '점수차': round(res['diff'], 2), '예상승률': round(_pwin(res['diff']), 3),
                          '내팀OVR': round(res['my_team_ovr'], 2) if res['my_team_ovr'] else None,
                          '상대팀OVR': round(res['opp_team_ovr'], 2) if res['opp_team_ovr'] else None,
                          '내선발손': msp[0].get('throw'), '상대선발손': osp[0].get('throw')})
        _update_player_scores(mine, res['my_sp_detail'], res['my_lineup_detail'])
        _update_player_scores(theirs, res['opp_sp_detail'], res['opp_lineup_detail'])
        summary['recalc'] += 1

    # ---------- 내 덱
    xd = read_sheet(wb, '내 덱')
    if xd is not None and deck:
        summary['deck_cells'] = _apply_deck(deck, xd, sn)

    # ---------- 저장 (백업 먼저)
    total = summary['match_cells'] + summary['player_cells'] + summary['deck_cells'] + summary['recalc']
    if total:
        bk = os.path.join(ST.DATA_DIR, '백업_' + ST.now().replace(':', '').replace(' ', '_').replace('-', ''))
        os.makedirs(bk, exist_ok=True)
        for f in (ST.MATCHES, ST.PLAYERS, ST.MY_DECK):
            if os.path.isfile(f):
                shutil.copy(f, bk)
        _write_jsonl(ST.MATCHES, matches)
        _write_jsonl(ST.PLAYERS, players)
        if summary['deck_cells']:
            ST.save_json(ST.MY_DECK, deck)
            pend = ST.load_json(ST.PENDING)
            if pend and pend.get('deck_time') == deck.get('time'):   # 아직 기록 안 한 분석도 고친 덱으로
                import copy
                pend['deck'] = copy.deepcopy(deck)
                ST.save_json(ST.PENDING, pend)
        summary['backup'] = bk
        summary['excel_error'] = ST.rebuild_excel()
    return summary


def _pwin(diff):
    import winmodel as W
    return W.p_win(diff)


def _update_player_scores(rows, sp_det, bat_det):
    sp_rows = [p for p in rows if p.get('역할') == '선발']
    bat_rows = [p for p in rows if p.get('역할') == '타자']
    for p, d, role in [(sp_rows[0], sp_det, '선발')] + [(b, bd, '타자') for b, bd in zip(bat_rows, bat_det)]:
        sks = (d.get('skills') or []) + [('', None, None)] * 3
        extra = d.get('angle', 0) + d.get('pen', 0) if role == '타자' else d.get('lefty', 0)
        for i in range(3):
            p[f'점수{i + 1}'] = sks[i][2] if p.get(f'스킬{i + 1}') else None
        p.update({'능력치점수': round(d['stat'], 2), '잠재점수': d['pot'], '기타보정': round(extra, 2),
                  '스킬합': round(d['skill'], 2), '매치업보정점수': round(d['total'], 2)})


def _apply_deck(deck, xd, sn):
    """내 덱 시트 -> my_deck.json (줄 순서: 타자 9명, 선발 5명)"""
    people = deck.get('batters', []) + deck.get('pitchers', [])
    n = 0

    def label(p):
        return f"{p.get('rotation')}선발" if p.get('pitcher') else p.get('pos')
    by = {str(r.get('포지션/선발') or '').strip(): r for r in xd}
    pairs = [(p, by.get(label(p))) for p in people]
    if any(r is None for _, r in pairs) and len(xd) == len(people):
        pairs = list(zip(people, xd))                  # 포지션 칸을 고쳤으면 줄 순서로 짝지음
    for p, r in pairs:
        if r is None:
            continue
        pit = bool(p.get('pitcher'))
        keys = ['구속', '변화', '구위', '제구', '지구력', '수비'] if pit else ['파워', '정확', '선구', '인내', '주루', '수비']
        before = [p.get(k) for k in keys]
        simple = [('이름', 'name'), ('OVR', 'ovr'), ('별', 'stars'), ('카드종류', 'card_type'), ('투', 'throw'), ('타', 'bat')]
        if not pit:
            simple += [('발사각', '발사각'), ('타순', 'slot')]
        for col, k in simple:
            if col in r:
                new = norm(col, r.get(col))
                if new != norm(col, p.get(k)):
                    p[k] = new
                    n += 1
        for col, k in zip(STAT_COLS, keys):
            if col in r:
                new = norm(col, r.get(col))
                if new != p.get(k):
                    p[k] = new
                    n += 1
        g = [norm('잠재1', r.get(c)) or '-' for c in ('잠재1', '잠재2', '잠재3', '잠재4', '잠재5', '각잠')]
        if any(c in r for c in ('잠재1', '각잠')) and g != [x or '-' for x in (p.get('grades') or ['-'] * 6)]:
            p['grades'] = g
            n += 1
        sk = []
        for i in (1, 2, 3):
            nm = r.get(f'스킬{i}')
            nm = str(nm).strip() if nm is not None else ''
            if nm:
                m, s = sn.match(nm, pit)
                sk.append((m if (m and s >= 0.8) else nm, norm('Lv1', r.get(f'Lv{i}'))))
        old_sk = [(a, b) for a, b in (p.get('skills') or []) if a]
        if any(f'스킬{i}' in r for i in (1, 2, 3)) and sk != old_sk:
            p['skills'] = sk
            n += 1
        fix_ovr(p, before)
    if n:
        for b in deck.get('batters', []):
            b['avg_score'] = SC.batter_score(b, {})['total']
        for p in deck.get('pitchers', []):
            p['avg_score'] = SC.pitcher_score(p, {})['total']
    return n


def _write_jsonl(path, rows):
    import json
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, path)

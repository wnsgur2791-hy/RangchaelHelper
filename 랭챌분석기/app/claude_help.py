# -*- coding: utf-8 -*-
"""Claude Code에게 맡기는 일 3가지
1) label_pending : 학습대기 그림을 한 장에 모아 보여 주고 정답을 받음 (일괄 학습)
2) verify_players: 확실하지 않게 읽힌 선수의 화면을 보여 주고 값을 바로잡음 (즉석 확인)
3) rescue / explain_screen: 막힌 화면을 보여 주고, 무슨 화면인지 + 어디를 눌러야 하는지 받음
"""
import os, re, json, shutil
from PIL import Image, ImageDraw, ImageFont

import recognize as R
import score as SC
import storage as ST
from claude_link import ClaudeLink, ClaudeError, WORK_DIR
from regions import DETAIL, ORDER, LOBBY, LINEUP

# ============================================================ 공통: 글꼴
_FONT = {}


def font(size):
    if size not in _FONT:
        f = None
        for p in ('C:/Windows/Fonts/malgunbd.ttf', 'C:/Windows/Fonts/malgun.ttf', 'C:/Windows/Fonts/arialbd.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
            if os.path.isfile(p):
                try:
                    f = ImageFont.truetype(p, size)
                    break
                except OSError:
                    pass
        if f is None:
            try:
                f = ImageFont.load_default(size=size)
            except TypeError:
                f = ImageFont.load_default()
        _FONT[size] = f
    return _FONT[size]


# ============================================================ 1) 일괄 학습
KIND_INFO = {
    'level': ('스킬 레벨 숫자', '1~10 사이 정수 (예: "7", "10")'),
    'grade': ('잠재력 등급 글자', '다음 중 하나: ' + ', '.join(R.GRADES) + ' ("+"가 오른쪽 위에 작게 붙어 있으면 +등급)'),
    'ovr_digit': ('카드 왼쪽 위 OVR 숫자 중 한 글자', '0~9 한 글자'),
    'angle_digit': ('평균 발사각 숫자 중 한 글자', '0~9 한 글자'),
    'label_char': ('선발 순번 라벨의 한 글자', '1, 2, 3, 4, 5, L, R 중 하나'),
    'card_type': ('카드 그림 아래쪽 배경에 흐리게 적힌 큰 글자(워터마크)',
                  '배경 글자로 판단: "KOREA" → 국대, "Signature" → 시그, "Golden Glove" → 골글, "IMPACT" → 임팩, '
                  '"ALL-STAR"/"ALL STAR" → 올스타, "LIVE" → 라이브. 답은 ' + ', '.join(R.CARD_TYPES) + ' 중 하나. '
                  '글자가 일부만 보여도 철자로 판단해(예: "KORE"가 보이면 국대). '
                  '배경 글자가 안 보이거나 위 목록에 없는 글자면 반드시 "?" (모르면 시그라고 하지 마)'),
}
PER_SHEET = {'card_type': 12}


def valid_answer(kind, ans):
    a = (ans or '').strip()
    if kind == 'card_type':
        return a if a in R.CARD_TYPES else None
    a = a.upper().replace(' ', '')
    ok = {'grade': a in R.GRADES,
          'level': a.isdigit() and 1 <= int(a) <= 10,
          'ovr_digit': len(a) == 1 and a.isdigit(),
          'angle_digit': len(a) == 1 and a.isdigit(),
          'label_char': a in ('1', '2', '3', '4', '5', 'L', 'R', 'S')}.get(kind, False)
    return a if ok else None


def load_pending():
    out = []
    if not os.path.isdir(R.PENDING_DIR):
        return out
    for fn in sorted(os.listdir(R.PENDING_DIR)):
        if not fn.endswith('.json'):
            continue
        key = fn[:-5]
        try:
            meta = json.load(open(os.path.join(R.PENDING_DIR, fn), encoding='utf-8'))
            img = Image.open(os.path.join(R.PENDING_DIR, key + '.png')).convert('RGB')
        except Exception:
            continue
        out.append({'key': key, 'kind': meta.get('kind'), 'guess': meta.get('guess') or '',
                    'context': meta.get('context', ''), 'img': img})
    return out


def make_sheet(items, tag):
    """그림 여러 개를 번호를 붙여 한 장에 모음"""
    kind = items[0]['kind']
    big = kind == 'card_type'
    cells = []
    binary = kind in ('ovr_digit', 'angle_digit', 'label_char')
    for it in items:
        im = it['img']
        if binary:                                  # 흰 글자/검은 바탕 마스크 -> 검은 글자/흰 바탕 + 여백
            from PIL import ImageOps
            im = ImageOps.expand(ImageOps.invert(im.convert('L')), border=max(2, im.height // 6), fill=255).convert('RGB')
        target_h = 70 if big else 90
        sc = max(1.0, target_h / max(1, im.height))
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.NEAREST)
        cells.append(im)
    cw = max(c.width for c in cells) + 24
    ch = max(c.height for c in cells) + 46
    cols = 3 if big else 6
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cw + 10, rows * ch + 10), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, c in enumerate(cells):
        x, y = 5 + (i % cols) * cw, 5 + (i // cols) * ch
        d.rectangle([x, y, x + cw - 6, y + ch - 6], outline=(200, 0, 0), width=2)
        d.text((x + 6, y + 3), f'#{i + 1}', fill=(200, 0, 0), font=font(24))
        sheet.paste(c, (x + 10, y + 34))
    name = ClaudeLink.save_image(sheet, tag)
    return name


def label_pending(link, items, log=print, chunk_sheets=6):
    """items 전부에 대해 Claude의 정답을 받아 {key: 정답 또는 None} 으로 돌려줌"""
    by_kind = {}
    for it in items:
        if it['kind'] in KIND_INFO:
            by_kind.setdefault(it['kind'], []).append(it)
    sheets = []                                     # (파일, kind, items)
    for kind, lst in by_kind.items():
        n = PER_SHEET.get(kind, 36)
        for s in range(0, len(lst), n):
            part = lst[s:s + n]
            sheets.append((make_sheet(part, f'학습_{kind}'), kind, part))
    answers = {}
    for c in range(0, len(sheets), chunk_sheets):
        group = sheets[c:c + chunk_sheets]
        lines = ['아래 그림 파일들은 내 프로그램이 게임 화면에서 잘라낸 작은 그림을 빨간 칸에 번호(#1, #2 ...)를 붙여 모은 거야.',
                 '각 칸의 그림이 무슨 글자/숫자인지 읽어 줘. 프로그램 추측은 틀릴 수 있으니 그림을 기준으로 판단해.',
                 '그림이 잘렸거나 알아볼 수 없으면 "?" 라고 적어.', '']
        for si, (fn, kind, part) in enumerate(group):
            what, fmt = KIND_INFO[kind]
            lines.append(f'[시트 {si + 1}] 파일: {fn}')
            lines.append(f'  내용: {what} / 답 형식: {fmt}')
            if kind == 'card_type':
                ref = reference_sheet()
                lines.append(f'  칸 수: {len(part)}' + (f' / 참고: 종류를 이미 아는 예시는 {ref[0]} 에 있어 ({ref[1]})' if ref else ''))
            else:
                guesses = ', '.join(f'#{i + 1}={p["guess"] or "없음"}' for i, p in enumerate(part))
                lines.append(f'  칸 수: {len(part)} / 프로그램 추측(틀릴 수 있음): {guesses}')
        lines += ['', '답 형식(JSON):',
                  '{"sheets": {"1": {"1": "답", "2": "답", ...}, "2": {...}}}',
                  '시트 번호와 칸 번호는 위의 번호 그대로 써.']
        n_items = sum(len(g[2]) for g in group)
        log(f'  Claude에게 {n_items}개 확인 요청 중... (시트 {len(group)}장, 보통 30초~2분)')
        data = link.ask_json('\n'.join(lines))
        sh = data.get('sheets', data) if isinstance(data, dict) else {}
        for si, (fn, kind, part) in enumerate(group):
            ans = sh.get(str(si + 1)) or sh.get(si + 1) or {}
            for i, it in enumerate(part):
                raw = ans.get(str(i + 1)) if isinstance(ans, dict) else None
                answers[it['key']] = valid_answer(kind, str(raw) if raw is not None else '')
    return answers


def reference_sheet():
    """카드 종류별로 이미 저장된 워터마크 예시를 A, B, C... 로 한 장에 모음 -> (파일, 'A=시그, B=골글 ...')"""
    d = os.path.join(R.TEMPLATE_DIR, 'ui')
    have = [(t, os.path.join(d, f'wm_{t}.png')) for t in R.CARD_TYPES if os.path.isfile(os.path.join(d, f'wm_{t}.png'))]
    if not have:
        return None
    ims = [Image.open(p).convert('RGB') for _, p in have]
    w = max(i.width for i in ims) + 70
    h = sum(i.height + 14 for i in ims) + 10
    sheet = Image.new('RGB', (w, h), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    y = 5
    names = []
    for k, ((t, _), im) in enumerate(zip(have, ims)):
        ch = chr(ord('A') + k)
        dr.text((8, y + im.height // 2 - 14), ch, fill=(0, 0, 200), font=font(28))
        sheet.paste(im, (60, y))
        names.append(f'{ch}={t}')
        y += im.height + 14
    return ClaudeLink.save_image(sheet, '카드종류_예시'), ', '.join(names)


def apply_label(item, ans):
    """정답을 템플릿에 추가하고 학습대기에서 지움 (gui의 학습 대기 확인과 같은 처리)"""
    kind = item['kind']
    if kind == 'card_type':
        p = os.path.join(R.TEMPLATE_DIR, 'ui', 'wm_' + ans + '.png')
        if not os.path.isfile(p):
            im = item['img']
            im.crop((8, 8, im.width - 8, im.height - 8)).save(p)
    elif ans != '-':
        R.store().add(kind, ans, item['img'])
    remove_pending(item['key'])


def remove_pending(key):
    for ext in ('.png', '.json'):
        try:
            os.remove(os.path.join(R.PENDING_DIR, key + ext))
        except OSError:
            pass


# ============================================================ 2) 즉석 확인
def player_sheet(shots, pitcher):
    """선수 상세 1, 2페이지에서 필요한 부분만 잘라 한 장으로"""
    parts = []
    p1, p2 = shots.get(1), shots.get(2)
    if p1 is not None:
        parts.append(('1페이지: 이름/포지션/발사각/스탯', p1.crop((60, 770, 860, 1305))))
    if p2 is not None:
        parts.append(('2페이지 위: 이름/투타', p2.crop((300, 120, 640, 236))))
        parts.append(('2페이지 카드 아래쪽: 카드종류 배경 글자', p2.crop((250, 540, 670, 700))))
        parts.append(('2페이지 아래: 잠재력 6칸/스킬 3개+레벨', p2.crop((80, 780, 850, 1275))))
    if not parts:
        return None
    w = max(p.width for _, p in parts) + 20
    h = sum(p.height + 40 for _, p in parts) + 10
    sheet = Image.new('RGB', (w, h), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    y = 5
    for title, p in parts:
        d.text((10, y), title, fill=(200, 0, 0), font=font(22))
        sheet.paste(p, (10, y + 32))
        y += p.height + 40
    return sheet


STAT_KEYS = {False: ['파워', '정확', '선구', '인내', '주루', '수비'], True: ['구속', '변화', '구위', '제구', '지구력', '수비']}


def _current(p):
    pit = bool(p.get('pitcher'))
    cur = {'이름': p.get('name'), '포지션': p.get('pos')}
    for k in STAT_KEYS[pit]:
        cur[k] = p.get(k)
    if not pit:
        cur['발사각'] = p.get('발사각')
    cur['잠재력'] = p.get('grades')
    cur['스킬'] = [list(s) for s in (p.get('skills') or [])]
    cur['투'] = p.get('throw')
    cur['타'] = p.get('bat')
    cur['카드종류'] = p.get('card_type')
    return cur


def pend_desc(pend):
    """학습대기에 들어간 항목 -> Claude에게 보여 줄 '확실하지 않은 것' 설명"""
    out = []
    for key, kind, ctx in pend or []:
        if kind == 'level':
            m = re.search(r'레벨(\d)', ctx)
            out.append(f'스킬{m.group(1)} 레벨' if m else '스킬 레벨')
        elif kind == 'angle_digit':
            out.append('발사각')
        elif kind == 'card_type':
            out.append('카드종류')
    return out


def verify_players(link, entries, sn, log=print, chunk=8):
    """entries: [(player dict, shots {1: img, 2: img}, ctx, pend)] -> 고친 개수. player dict를 직접 고침
    pend = 그 선수를 읽는 동안 학습대기에 들어간 것 [(key, kind, ctx)] -> Claude 확인 뒤 자동 학습
    한 번에 너무 많이 보내지 않도록 chunk명씩 나눠서 물어봄"""
    entries = [e if len(e) == 4 else (*e, []) for e in entries]
    if len(entries) > chunk:
        return sum(verify_players(link, entries[i:i + chunk], sn, log, chunk) for i in range(0, len(entries), chunk))
    if not entries:
        return 0
    lines = ['내 프로그램이 선수 상세 화면을 글자 인식으로 읽었는데, 확실하지 않은 값이 있어.',
             '선수마다 화면 일부를 잘라 모은 그림과 프로그램이 읽은 값을 줄게. 그림을 보고 틀리거나 비어 있는 값을 바로잡아 줘.',
             '그리고 프로그램이 왜 헷갈렸을 것 같은지 너의 진단도 한두 문장으로 적어 줘 (프로그램을 고칠 때 참고할 거야).',
             '화면 설명:',
             ' - 1페이지 스탯 칸: 왼쪽 열 위→아래, 오른쪽 열 위→아래 순서. 타자=파워/정확/선구, 인내/주루/수비. 투수=구속/변화/구위, 제구/지구력/수비.',
             '   스탯은 세 자리(예: 171)일 수 있으니 앞자리 1을 빠뜨리지 마. 분홍 막대가 길수록 큰 값이야.',
             ' - 평균 발사각: 1페이지 오른쪽 "평균 발사각" 칸의 숫자 (도).',
             ' - 잠재력 6칸: 왼쪽 열 위→아래가 1,2,3번, 오른쪽 열 위→아래가 4,5,6번. 등급은 ' + ', '.join(R.GRADES) + ' 중 하나, 빈 칸은 "-".',
             ' - 스킬 3개: 왼쪽→오른쪽 순서, 각 스킬 이름 아래 "Lv.숫자"가 레벨(1~10).',
             ' - 투타: 2페이지 위 "우투좌타" 같은 글자. 투 = L(좌투)/R(우투), 타 = L(좌타)/R(우타)/S(양타).',
             ' - 카드종류: 카드 그림 아래쪽 배경에 흐리게 적힌 큰 글자. ' + KIND_INFO['card_type'][1],
             '']
    files = {}
    for i, (p, shots, ctx, pend) in enumerate(entries):
        pit = bool(p.get('pitcher'))
        sheet = player_sheet(shots, pit)
        if sheet is None:
            continue
        fn = ClaudeLink.save_image(sheet, f'확인_{i + 1}')
        files[i] = fn
        names = SC.skill_names('투수' if pit else '타자')
        why = list(p.get('warn') or []) + pend_desc(pend)
        lines.append(f'[선수 {i + 1}] 그림: {fn} / {"투수" if pit else "타자"} / 위치: {ctx}')
        lines.append('  프로그램이 읽은 값: ' + json.dumps(_current(p), ensure_ascii=False))
        lines.append('  확실하지 않다고 표시된 것: ' + ', '.join(why))
        if any(str(w).startswith('스킬') for w in why):
            lines.append('  (스킬 이름은 가능하면 이 목록 중에서: ' + ', '.join(names) + ')')
    lines += ['', '답 형식(JSON). "고칠값"에는 바꿀 값만 적고 맞는 값은 적지 마. 단 "카드종류"는 그림에서 보이면 항상 적어:',
              '{"players": {"1": {"고칠값": {"파워": 171, "발사각": 12, "잠재력": ["A","B+","-","-","-","S"], '
              '"스킬": [["좌완킬러", 7], ["...", 5], ["...", 3]], "투": "R", "타": "L", "카드종류": "국대"}, '
              '"진단": "파워 칸의 앞자리 1이 흐려서 빠진 것 같음"}, "2": {"고칠값": {}, "진단": "..."}}}',
              '그림으로도 알 수 없는 값은 적지 마.']
    log(f'  Claude에게 확실하지 않은 선수 {len(entries)}명 확인 요청 중... (보통 10초~1분)')
    data = link.ask_json('\n'.join(lines))
    fixes = data.get('players', data) if isinstance(data, dict) else {}
    changed = 0
    for i, (p, shots, ctx, pend) in enumerate(entries):
        ans = fixes.get(str(i + 1)) or fixes.get(i + 1) or {}
        if not isinstance(ans, dict):
            continue
        fx = ans.get('고칠값') if isinstance(ans.get('고칠값'), dict) else {k: v for k, v in ans.items() if k != '진단'}
        before_warn = list(p.get('warn') or []) + pend_desc(pend)
        notes = _apply_fix(p, fx, sn)
        p['claude_checked'] = True
        p['warn'] = []
        learned = learn_from_player(p, pend, fx)
        diag = str(ans.get('진단') or '').strip()
        if notes:
            changed += 1
            p['claude_fix'] = notes
            log(f"  ✓ Claude 확인 {p.get('name')}: " + ', '.join(notes) + (f' (자동 학습 {learned}개)' if learned else ''))
        else:
            log(f"  ✓ Claude 확인 {p.get('name')}: 프로그램이 읽은 값이 맞아요" + (f' (자동 학습 {learned}개)' if learned else ''))
        if diag:
            log(f'    [Claude 진단] {diag}')
        write_memo('값 확인', ctx, p.get('name'), before_warn, notes, diag, files.get(i), learned)
    return changed


def learn_from_player(p, pend, fx):
    """Claude가 확인한 최종 값으로, 그 선수를 읽을 때 학습대기에 들어간 그림에 정답을 붙여 바로 학습"""
    n = 0
    for key, kind, ctx in pend or []:
        ans = None
        if kind == 'level':
            m = re.search(r'레벨(\d)', ctx)
            sk = p.get('skills') or []
            if m and int(m.group(1)) - 1 < len(sk):
                lv = sk[int(m.group(1)) - 1][1]
                ans = str(lv) if lv else None
        elif kind == 'angle_digit':
            m = re.search(r'#(\d+)/(\d+)', ctx)
            v = p.get('발사각')
            if m and v is not None and len(str(v)) == int(m.group(2)):
                ans = str(v)[int(m.group(1))]
        elif kind == 'card_type':
            ans = fx.get('카드종류') if fx.get('카드종류') in R.CARD_TYPES else None   # Claude가 직접 본 경우만
        ans = valid_answer(kind, ans) if ans else None
        if not ans:
            continue
        try:
            img = Image.open(os.path.join(R.PENDING_DIR, key + '.png')).convert('RGB')
        except Exception:
            continue
        apply_label({'key': key, 'kind': kind, 'img': img}, ans)
        n += 1
    if n:
        R.store().reload()
        R._UI_CACHE.clear()
    return n


MEMO = os.path.join(ST.ROOT_DIR, '진단', 'Claude_진단메모.txt')
MEMO_IMG = os.path.join(ST.ROOT_DIR, '진단', 'Claude_화면')


def write_memo(what, ctx, name, flagged, fixed, diag, img_file=None, learned=0):
    """Claude의 확인 결과와 오류에 대한 견해를 메모장 파일에 쌓아 둠"""
    try:
        os.makedirs(MEMO_IMG, exist_ok=True)
        keep = ''
        if img_file and os.path.isfile(os.path.join(WORK_DIR, img_file)):
            shutil.copy(os.path.join(WORK_DIR, img_file), os.path.join(MEMO_IMG, img_file))
            keep = os.path.join('진단', 'Claude_화면', img_file)
        lines = [f'[{ST.now()}] {what} — {ctx or ""} {name or ""}'.rstrip()]
        if flagged:
            lines.append('  확인 필요였던 것: ' + ', '.join(str(x) for x in flagged))
        lines.append('  Claude가 고친 값: ' + (', '.join(fixed) if fixed else '없음 (프로그램이 읽은 값이 맞음)'))
        if learned:
            lines.append(f'  자동 학습: {learned}개')
        if diag:
            lines.append('  Claude 의견: ' + diag)
        if keep:
            lines.append('  화면: ' + keep)
        with open(MEMO, 'a', encoding='utf-8-sig' if not os.path.isfile(MEMO) else 'utf-8') as f:
            f.write('\r\n'.join(lines) + '\r\n\r\n')
    except OSError:
        pass


def _int(v, lo, hi):
    try:
        v = int(str(v).strip().rstrip('°'))
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _apply_fix(p, fx, sn):
    pit = bool(p.get('pitcher'))
    notes = []

    def setv(k, v):
        if v is not None and p.get(k) != v:
            notes.append(f'{k} {p.get(k)}→{v}')
            p[k] = v
    for k in STAT_KEYS[pit]:
        if k in fx:
            setv(k, _int(fx[k], 1, 250))
    if not pit and '발사각' in fx:
        setv('발사각', _int(fx['발사각'], 0, 40))
    if '이름' in fx and fx['이름']:
        old = p.get('name')
        setv('name', R.clean_name(str(fx['이름'])) or p.get('name'))
        R.add_name_fix(old, p.get('name'))             # 다음부터 같은 오독은 자동으로 고침
    if '포지션' in fx and str(fx['포지션']).upper() in R.POSITIONS:
        setv('pos', str(fx['포지션']).upper())
    g = fx.get('잠재력')
    if isinstance(g, list) and len(g) == 6:
        gg = [str(x).strip().upper() if str(x).strip().upper() in R.GRADES else '-' for x in g]
        if gg != p.get('grades'):
            notes.append('잠재력 ' + '/'.join(gg))
            p['grades'] = gg
    s = fx.get('스킬')
    if isinstance(s, list) and s:
        old = list(p.get('skills') or [])
        new = []
        for j in range(3):
            o = tuple(old[j]) if j < len(old) else ('', None)
            if j < len(s) and isinstance(s[j], (list, tuple)) and len(s[j]) >= 1:
                nm = str(s[j][0] or '').strip()
                m, sc = sn.match(nm, pit) if nm else (None, 0)
                nm = m if (m and sc >= 0.6) else (nm or o[0])
                lv = _int(s[j][1], 1, 10) if len(s[j]) > 1 else None
                new.append((nm, lv if lv is not None else o[1]))
            else:
                new.append(o)
        if [tuple(x) for x in old] != new:
            notes.append('스킬 ' + ', '.join(f'{a} Lv{b}' for a, b in new))
            p['skills'] = new
    t = str(fx.get('투') or '').upper()
    if t in ('L', 'R'):
        setv('throw', t)
    b = str(fx.get('타') or '').upper()
    if b in ('L', 'R', 'S'):
        setv('bat', b)
    if fx.get('카드종류') in R.CARD_TYPES:
        setv('card_type', fx.get('카드종류'))
    vals = [p.get(k) for k in STAT_KEYS[pit]]
    if all(v is not None for v in vals):
        p['ovr'] = sum(vals) // 6
    return notes


# ============================================================ 3) 막힌 화면 도움
BUTTONS = {
    '상세화면_닫기X': DETAIL['close'], '상세화면_다음선수»': DETAIL['next_player'],
    '상세화면_페이지오른쪽»': DETAIL['page_right'], '상세화면_페이지왼쪽«': DETAIL['page_left'],
    '순서변경화면_닫기': ORDER['close'], '로비_순서변경버튼': LOBBY['order_button'],
    '라인업_타자탭': LINEUP['tab_batter'], '라인업_투수탭': LINEUP['tab_pitcher'],
}


def _shot(img, tag):
    return ClaudeLink.save_image(img, tag)


def explain_screen(link, img, goal, err):
    """지금 화면 설명 + 사용자에게 할 말 (누르지는 않음)"""
    fn = _shot(img, '막힘')
    prompt = (f'그림 파일: {fn} (크기 {img.width}x{img.height})\n'
              f'내 프로그램이 하려던 일: {goal}\n프로그램이 낸 오류: {err}\n'
              '그림을 보고 (1) 지금 무슨 화면인지, (2) 프로그램이 왜 막혔을 것 같은지, '
              '(3) 사용자가 무엇을 하면 되는지 짧게 한국어로 알려 줘.\n'
              '답 형식(JSON): {"screen": "...", "why": "...", "advice": "..."}')
    return link.ask_json(prompt), fn


def rescue(link, game, img, goal, err, is_done, log=print, steps=3):
    """Claude가 알려 주는 곳을 눌러 목표 화면으로 돌아가기. is_done(img) -> 목표 도착 여부"""
    for step in range(steps):
        fn = _shot(img, f'막힘{step + 1}')
        btns = '\n'.join(f'  - {k}: {v}' for k, v in BUTTONS.items())
        prompt = (f'그림 파일: {fn} (크기 {img.width}x{img.height}, 좌표는 이 그림의 픽셀 기준)\n'
                  f'내 프로그램이 하려던 일: {goal}\n프로그램이 낸 오류: {err}\n'
                  f'이번이 {step + 1}번째 도움 요청이야.\n'
                  '그림을 보고 지금 무슨 화면인지 판단하고, 목표 화면으로 가려면 다음에 한 번 눌러야 할 곳을 알려 줘.\n'
                  '자주 쓰는 버튼 위치(이 그림 기준 좌표):\n' + btns + '\n'
                  '목록에 없는 곳(팝업의 확인/닫기 등)을 눌러야 하면 그 위치의 x, y를 직접 적어.\n'
                  '게임 진행을 바꾸는 버튼(경기 시작 PLAY BALL, 갱신, 구매, 저장 등)은 절대 누르라고 하지 마.\n'
                  '이미 목표 화면이면 action을 "done"으로.\n'
                  '답 형식(JSON): {"screen": "지금 화면", "why": "막힌 이유 추측", '
                  '"action": "tap" 또는 "done" 또는 "stop", "button": "버튼 이름 또는 null", "x": 0, "y": 0, '
                  '"advice": "사용자에게 할 말"}')
        data = link.ask_json(prompt)
        log(f"  [Claude] 화면: {data.get('screen')} / {data.get('why') or ''}")
        act = str(data.get('action') or '').lower()
        did = f"{data.get('button') or (data.get('x'), data.get('y'))} 누름" if act == 'tap' else act
        write_memo('화면 막힘', goal, '', [err], [did],
                   ' / '.join(str(x) for x in (data.get('screen'), data.get('why'), data.get('advice')) if x), fn)
        if act == 'done':
            return True, data
        if act != 'tap':
            if data.get('advice'):
                log(f"  [Claude] {data.get('advice')}")
            return False, data
        pt = BUTTONS.get(data.get('button') or '')
        if pt is None:
            x, y = _int(data.get('x'), 0, img.width), _int(data.get('y'), 0, img.height)
            if x is None or y is None:
                return False, data
            pt = (x, y)
        log(f"  [Claude] {data.get('button') or ''} {pt} 누르기")
        game.click(pt)
        game.sleep(1.5)
        img = game.grab()
        if is_done(img):
            return True, data
    return False, None


# ============================================================ 4) 학습 내용 점검
AUDIT_KINDS = ('level', 'grade', 'ovr_digit', 'angle_digit', 'label_char')


def load_templates():
    """지금까지 학습된 그림(템플릿) 전부: [{'key': 파일경로, 'kind', 'label', 'img'}]"""
    out = []
    for kind in AUDIT_KINDS:
        d = os.path.join(R.TEMPLATE_DIR, kind)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.png'):
                continue
            try:
                img = Image.open(os.path.join(d, fn)).convert('RGB')
            except Exception:
                continue
            out.append({'key': os.path.join(d, fn), 'kind': kind, 'label': fn.split('__')[0].replace('plus', '+'),
                        'guess': '', 'context': '', 'img': img})
    d = os.path.join(R.TEMPLATE_DIR, 'ui')
    for t in R.CARD_TYPES:
        p = os.path.join(d, f'wm_{t}.png')
        if os.path.isfile(p):
            out.append({'key': p, 'kind': 'card_type', 'label': t, 'guess': '', 'context': '',
                        'img': Image.open(p).convert('RGB')})
    return out


def audit_templates(link, log=print):
    """Claude가 정답을 모르는 상태로 직접 읽게 한 뒤, 저장된 정답과 다른 것만 돌려줌"""
    items = load_templates()
    log(f'  학습된 그림 {len(items)}개를 Claude가 직접 읽어서 저장된 정답과 비교할게요.')
    answers = label_pending(link, items, log)
    flagged = []
    for it in items:
        a = answers.get(it['key'])
        if a != it['label']:
            it['claude'] = a                 # None = Claude도 알아보기 어려움
            flagged.append(it)
    return items, flagged


def relabel_template(item, new_label):
    """학습된 그림의 정답 바꾸기 (파일 이름의 정답 부분만 바꿈)"""
    d, fn = os.path.split(item['key'])
    rest = fn.split('__', 1)[1] if '__' in fn else fn
    dst = os.path.join(d, new_label.replace('+', 'plus') + '__' + rest)
    os.replace(item['key'], dst)
    item['key'], item['label'] = dst, new_label


def delete_template(item):
    try:
        os.remove(item['key'])
    except OSError:
        pass


# ============================================================ 5) 선발 번호 라벨
def read_rotation_labels(link, crops):
    """'3선발 R' 같은 라벨 그림 5개 -> [3, ...] (못 읽으면 None)"""
    cells = [c.resize((c.width * 2, c.height * 2), Image.LANCZOS) for c in crops]
    w = max(c.width for c in cells) + 90
    h = sum(c.height + 12 for c in cells) + 10
    sheet = Image.new('RGB', (w, h), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    y = 5
    for i, c in enumerate(cells):
        d.text((8, y + c.height // 2 - 14), f'#{i + 1}', fill=(200, 0, 0), font=font(26))
        sheet.paste(c, (80, y))
        y += c.height + 12
    fn = ClaudeLink.save_image(sheet, '선발라벨')
    data = link.ask_json(f'그림 파일: {fn}\n'
                         '#1~#5 줄마다 파란 라벨에 "3선발 R" 같은 글자가 있어. 각 줄의 선발 번호(1~5) 숫자만 읽어 줘. '
                         '안 보이면 null.\n답 형식(JSON): {"labels": [1, 2, 3, 4, 5]}')
    out = []
    for v in (data.get('labels') or [])[:len(crops)]:
        out.append(_int(v, 1, 5))
    return out + [None] * (len(crops) - len(out))

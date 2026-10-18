# -*- coding: utf-8 -*-
"""매주 경기 기록 보내기 (개발자 디스코드 채널로)

- 시즌은 월요일 0시에 끝나므로, '월요일 0시 ~ 다음 월요일 0시'를 한 주로 봄.
  끝난 주 중에 아직 안 보낸 주가 있으면 그 주 기록만 묶어서 보냄 (프로그램을 켜 두면 6시간마다 확인).
- 보내는 것: 그 주의 매치기록(matches) + 선수기록(players). 상대 닉네임은 지우고 보냄.
  보내는 사람은 이름 대신 무작위 번호(예: a3f9c2d1)로만 구분.
- 처음 한 번 보낼지 물어보고, [설정] 메뉴에서 언제든 끌 수 있음.
- 받는 주소(디스코드 웹훅)는 GitHub 저장소의 config.json에서 읽음 -> 주소를 바꿔도 프로그램 업데이트가 필요 없음.
"""
import os, io, json, uuid, base64, zipfile, datetime, urllib.request

import storage as ST

STATE = os.path.join(ST.DATA_DIR, 'share_state.json')
DROP_KEYS = ('상대닉네임',)                     # 보내기 전에 지우는 칸
UA = 'RangchaelHelper (https://github.com, 1.0)'


def state():
    st = ST.load_json(STATE, None) or {}
    changed = False
    if not st.get('user_id'):
        st['user_id'] = uuid.uuid4().hex[:8]
        changed = True
    st.setdefault('consent', None)               # None = 아직 안 물어봄 / True / False
    st.setdefault('sent_weeks', [])
    if changed:
        ST.save_json(STATE, st)
    return st


def set_consent(on):
    st = state()
    st['consent'] = bool(on)
    ST.save_json(STATE, st)


def week_start(ts):
    """'2026-10-14 21:03:11' -> 그 주 월요일 '2026-10-12'"""
    try:
        d = datetime.datetime.strptime(str(ts)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def finished_weeks(now=None):
    """기록이 있는 주 중 이미 끝난 주 (월요일 날짜 목록, 오래된 순)"""
    now = now or datetime.datetime.now()
    this_monday = (now.date() - datetime.timedelta(days=now.weekday())).isoformat()
    weeks = {week_start(r.get('기록시각')) for r in ST.read_jsonl(ST.MATCHES)}
    return sorted(w for w in weeks if w and w < this_monday)


def clean(row):
    return {k: v for k, v in row.items() if k not in DROP_KEYS}


def build_week(week):
    """그 주 기록을 zip(바이트)으로. (zip, 경기 수)"""
    matches = [clean(r) for r in ST.read_jsonl(ST.MATCHES) if week_start(r.get('기록시각')) == week]
    if not matches:
        return None, 0
    times = {r.get('기록시각') for r in matches}
    players = [clean(r) for r in ST.read_jsonl(ST.PLAYERS) if r.get('기록시각') in times]
    st = state()
    try:
        from version import VERSION
    except Exception:
        VERSION = '?'
    info = {'user_id': st['user_id'], 'week': week, 'matches': len(matches), 'players': len(players),
            'version': VERSION, 'sent_at': ST.now()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('info.json', json.dumps(info, ensure_ascii=False, indent=1))
        z.writestr('matches.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in matches))
        z.writestr('players.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in players))
    return buf.getvalue(), len(matches)


def webhook_url(timeout=8):
    """GitHub 저장소 config.json 의 디스코드 주소 (글자를 base64로 감싸 둠). 없으면 None"""
    from version import REPO
    url = f'https://raw.githubusercontent.com/{REPO}/main/config.json'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cfg = json.loads(r.read().decode('utf-8'))
    b = (cfg.get('discord_webhook_b64') or '').strip()
    if not b:
        return None
    u = base64.b64decode(b).decode('utf-8').strip()
    return u if u.startswith('https://') else None


def multipart(fields, files):
    """디스코드에 파일을 올리는 형식(multipart/form-data)으로 묶기. files: [(필드, 파일이름, 바이트, 종류)]"""
    bnd = '----rangchal' + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n'
                  f'Content-Type: application/json\r\n\r\n'.encode('utf-8'))
        out.write(v.encode('utf-8') + b'\r\n')
    for field, fname, data, ctype in files:
        out.write(f'--{bnd}\r\nContent-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                  f'Content-Type: {ctype}\r\n\r\n'.encode('utf-8'))
        out.write(data + b'\r\n')
    out.write(f'--{bnd}--\r\n'.encode('utf-8'))
    return out.getvalue(), f'multipart/form-data; boundary={bnd}'


def post_week(url, week, data, n, timeout=30):
    st = state()
    fname = f'rankchal_{st["user_id"]}_{week}.zip'
    payload = json.dumps({'content': f'사용자 `{st["user_id"]}` · {week} 주 · {n}경기',
                          'allowed_mentions': {'parse': []}}, ensure_ascii=False)
    body, ctype = multipart({'payload_json': payload}, [('files[0]', fname, data, 'application/zip')])
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': ctype, 'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


def send_pending(log=None, max_weeks=4):
    """동의했으면 안 보낸 지난주 기록을 보냄. 보낸 주 수를 돌려줌"""
    st = state()
    if st.get('consent') is not True:
        return 0
    todo = [w for w in finished_weeks() if w not in st['sent_weeks']][:max_weeks]
    if not todo:
        return 0
    try:
        url = webhook_url()
    except Exception:
        return 0                                    # 인터넷이 안 되면 다음에 다시
    if not url:
        return 0
    sent = 0
    for w in todo:
        data, n = build_week(w)
        if not data:
            st['sent_weeks'].append(w)
            continue
        try:
            ok = post_week(url, w, data, n)
        except Exception as e:
            if log:
                log(f'  (지난주 기록 보내기 실패: {e.__class__.__name__}. 다음에 다시 보낼게요)')
            break
        if ok:
            st['sent_weeks'].append(w)
            sent += 1
            if log:
                log(f'  {w} 주 경기 기록 {n}경기를 보냈어요. (닉네임 제외)')
    ST.save_json(STATE, st)
    return sent

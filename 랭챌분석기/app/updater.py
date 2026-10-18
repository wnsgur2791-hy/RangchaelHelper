# -*- coding: utf-8 -*-
"""자동 업데이트 (파이썬 기본 기능만 사용 - 다른 부품이 없어도 돌아가게)

동작 순서 (실행.bat -> launcher.py 가 프로그램을 켜기 직전에 부름)
1. GitHub 저장소의 '최신 릴리스'를 물어봄 (1초 정도)
2. 내 버전보다 새 버전이면 update.zip(코드만, 1~2MB)을 받음
3. 지금 app 폴더를 '업데이트백업' 폴더에 복사해 둔 뒤 새 파일로 바꿈
   - 기록데이터, 랭챌기록.xlsx, 설정.json, 진단, 학습대기는 절대 건드리지 않음 (update.zip에 들어 있지도 않음)
   - 학습 그림(app/templates)은 덮어쓰지 않고 '없는 것만' 추가 -> 내가 학습시킨 그림은 그대로
   - 이름 고침표(name_fix.json)는 합침 -> 내가 추가한 고침은 그대로
   - .bat 파일은 실행 중에 바꾸면 윈도우가 이상하게 동작해서 바꾸지 않음
4. 새 버전이 켜지고 3초가 지나면 '정상 실행' 표시를 남김.
   새 버전이 켜지다 죽으면, 다음에 켤 때 백업으로 되돌림 (같은 버전이 두 번 실패하면 그 버전은 건너뜀)
"""
import os, sys, io, json, time, shutil, zipfile, datetime, subprocess
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
BACKUP_DIR = os.path.join(ROOT_DIR, '업데이트백업')
MARKER = os.path.join(BACKUP_DIR, '확인대기.json')
SKIP = os.path.join(BACKUP_DIR, '건너뛸버전.json')
ROOT_FILES = ('설명서.txt', '처음 읽어주세요.txt', 'requirements.txt')     # 루트에서 바꿔도 되는 파일
UA = 'RangchaelHelper-updater'


def current_version():
    try:
        sys.path.insert(0, APP_DIR)
        import importlib
        import version
        importlib.reload(version)
        return version.VERSION, version.REPO
    except Exception:
        return '0.0.0', 'wnsgur2791-hy/RangchaelHelper'


def vtuple(v):
    v = str(v or '').strip().lstrip('vV')
    out = []
    for p in v.split('.'):
        num = ''.join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple((out + [0, 0, 0])[:3])


def _load(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def enabled():
    s = _load(os.path.join(ROOT_DIR, '설정.json'), {}) or {}
    return s.get('자동_업데이트', True) is not False


def http_get(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_release(repo, timeout=6):
    """{'version': '1.0.1', 'notes': '...', 'update_url': ..., 'full_url': ...} 또는 None"""
    data = json.loads(http_get(f'https://api.github.com/repos/{repo}/releases/latest', timeout).decode('utf-8'))
    assets = {a.get('name'): a.get('browser_download_url') for a in data.get('assets') or []}
    return {'version': (data.get('tag_name') or '').lstrip('vV'), 'notes': data.get('body') or '',
            'update_url': assets.get('update.zip'), 'full_url': assets.get('RangchaelHelper.zip'),
            'page': data.get('html_url')}


def check(log=print):
    """새 버전이 있으면 정보, 없으면 None (받거나 바꾸지는 않음)"""
    cur, repo = current_version()
    rel = latest_release(repo)
    if not rel or not rel['version'] or vtuple(rel['version']) <= vtuple(cur):
        return None
    if (_load(SKIP, {}) or {}).get(rel['version'], 0) >= 2:
        log(f'  (v{rel["version"]}은 이 PC에서 두 번 켜지지 않아서 건너뛰어요)')
        return None
    return rel


# ------------------------------------------------------------ 적용
def _backup(cur):
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(BACKUP_DIR, f'v{cur}_{stamp}')
    k = 1
    while os.path.exists(dst):                       # 같은 초에 두 번 백업하는 경우
        dst = os.path.join(BACKUP_DIR, f'v{cur}_{stamp}_{k}')
        k += 1
    shutil.copytree(APP_DIR, os.path.join(dst, 'app'), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for fn in ROOT_FILES:
        p = os.path.join(ROOT_DIR, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, fn))
    # 백업은 최근 2개만 남김
    olds = sorted(d for d in os.listdir(BACKUP_DIR) if d.startswith('v') and os.path.isdir(os.path.join(BACKUP_DIR, d)))
    for d in olds[:-2]:
        shutil.rmtree(os.path.join(BACKUP_DIR, d), ignore_errors=True)
    return dst


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.새버전'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _safe_name(name):
    name = name.replace('\\', '/')
    if name.startswith('/') or '..' in name.split('/') or ':' in name:
        return None
    return name


def apply_zip(raw, new_ver, notes='', log=print):
    """update.zip 내용을 프로그램 폴더에 반영. 반영했으면 True"""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in zf.namelist() if not n.endswith('/')]
    if zf.testzip() is not None or 'app/gui.py' not in names or 'app/version.py' not in names:
        raise RuntimeError('받은 업데이트 파일이 깨져 있어요')
    cur, _ = current_version()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup = _backup(cur)
    old_req = _read_text(os.path.join(ROOT_DIR, 'requirements.txt'))
    n_new = n_tpl = 0
    for name in names:
        rel = _safe_name(name)
        if not rel:
            continue
        top = rel.split('/')[0]
        if top != 'app' and rel not in ROOT_FILES:
            continue                                   # .bat, 기록데이터 등은 건너뜀
        if '__pycache__' in rel or rel.endswith('.pyc'):
            continue
        dst = os.path.join(ROOT_DIR, *rel.split('/'))
        data = zf.read(name)
        if rel.startswith('app/templates/'):
            if not os.path.exists(dst):                # 학습 그림: 없는 것만 추가
                _write(dst, data)
                n_tpl += 1
            continue
        if rel == 'app/data/name_fix.json' and os.path.isfile(dst):
            mine = _load(dst, {}) or {}
            try:
                theirs = json.loads(data.decode('utf-8'))
            except Exception:
                theirs = {}
            for k, v in theirs.items():
                mine.setdefault(k, v)                  # 내가 넣은 고침이 우선
            _save(dst, mine)
            continue
        _write(dst, data)
        n_new += 1
    _clear_pycache()
    _save(MARKER, {'from': cur, 'to': new_ver, 'backup': backup, 'notes': notes,
                   'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    log(f'  v{cur} → v{new_ver} 업데이트 완료 (파일 {n_new}개, 새 학습 그림 {n_tpl}개). 이전 버전은 업데이트백업 폴더에 있어요.')
    if _read_text(os.path.join(ROOT_DIR, 'requirements.txt')) != old_req:
        log('  필요한 부품이 바뀌어서 설치할게요... (1~3분)')
        try:
            subprocess.call([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(ROOT_DIR, 'requirements.txt')],
                            timeout=900)
        except Exception as e:
            log(f'  ⚠ 부품 설치 실패: {e} (설치.bat을 한 번 실행해 주세요)')
    return True


def _clear_pycache():
    """파이썬이 만들어 둔 미리 읽은 파일(__pycache__)을 지움 -> 다음 실행 때 새 코드를 확실히 읽게"""
    shutil.rmtree(os.path.join(APP_DIR, '__pycache__'), ignore_errors=True)


def _read_text(p):
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


def auto_update(log=print):
    """켤 때 한 번: 새 버전이 있으면 받아서 바로 바꿈. 바꿨으면 새 버전 번호"""
    if not enabled():
        return None
    try:
        rel = check(log)
    except Exception as e:
        log(f'  (업데이트 확인을 못 했어요: {e.__class__.__name__}. 지금 버전으로 켤게요)')
        return None
    if not rel:
        return None
    if not rel.get('update_url'):
        log(f'  새 버전 v{rel["version"]}이 있지만 업데이트 파일이 없어요. 지금 버전으로 켤게요.')
        return None
    log(f'  새 버전 v{rel["version"]}을 받는 중...')
    try:
        raw = http_get(rel['update_url'], timeout=60)
        apply_zip(raw, rel['version'], rel.get('notes', ''), log)
        return rel['version']
    except Exception as e:
        log(f'  ⚠ 업데이트 실패: {e} (지금 버전으로 켤게요)')
        return None


# ------------------------------------------------------------ 정상 실행 확인 / 되돌리기
def confirm_ok():
    """새 버전 화면이 정상으로 떴을 때 부름. 방금 업데이트했다면 그 정보를 돌려줌"""
    m = _load(MARKER)
    if m is None:
        return None
    try:
        os.remove(MARKER)
    except OSError:
        pass
    return m


def rollback_if_needed(log=print):
    """'확인대기' 표시가 남아 있으면 = 지난번 새 버전이 정상으로 켜지지 못함 -> 백업으로 되돌림"""
    m = _load(MARKER)
    if not m:
        return False
    backup = m.get('backup')
    if not backup or not os.path.isdir(os.path.join(backup, 'app')):
        try:
            os.remove(MARKER)
        except OSError:
            pass
        return False
    log(f'  ⚠ v{m.get("to")}이 정상으로 켜지지 않았던 것 같아서 v{m.get("from")}으로 되돌릴게요.')
    src = os.path.join(backup, 'app')
    for base, dirs, files in os.walk(src):
        rel = os.path.relpath(base, src)
        for fn in files:
            dst = os.path.join(APP_DIR, rel, fn) if rel != '.' else os.path.join(APP_DIR, fn)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(base, fn), dst)
    for fn in ROOT_FILES:
        p = os.path.join(backup, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(ROOT_DIR, fn))
    _clear_pycache()
    skip = _load(SKIP, {}) or {}
    skip[m.get('to')] = skip.get(m.get('to'), 0) + 1
    _save(SKIP, skip)
    try:
        os.remove(MARKER)
    except OSError:
        pass
    return True

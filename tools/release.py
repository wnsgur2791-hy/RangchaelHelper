# -*- coding: utf-8 -*-
"""새 버전 릴리스 만들기

사용:  python tools/release.py 1.0.1 "바뀐 내용 한 줄" ["바뀐 내용 두 번째 줄" ...]
       (--dry 를 붙이면 zip만 만들고 올리지 않음)

하는 일
1. 랭챌분석기/app/version.py 의 VERSION 을 바꿈
2. dist/update.zip          : 자동 업데이트용 (app 폴더 + 설명서/처음 읽어주세요/requirements)
   dist/RangchaelHelper.zip : 처음 받는 사람용 전체 (랭챌분석기 폴더 통째, 언어 데이터 포함)
3. GitHub 릴리스 vX.Y.Z 를 만들고 두 파일을 붙임 (gh CLI 필요)
"""
import os, re, sys, json, zipfile, subprocess

REPO = 'wnsgur2791-hy/RangchaelHelper'
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(HERE, '랭챌분석기')
DIST = os.path.join(HERE, 'dist')
SKIP_DIRS = {'__pycache__', '기록데이터', '진단', '학습대기', '클로드작업', '업데이트백업'}
SKIP_FILES = {'랭챌기록.xlsx', '설정.json'}
UPDATE_ROOT_FILES = ('설명서.txt', '처음 읽어주세요.txt', 'requirements.txt')


def walk(base):
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f in SKIP_FILES or f.endswith('.pyc') or f.endswith('.tmp'):
                continue
            yield os.path.join(root, f)


def set_version(v):
    p = os.path.join(PROG, 'app', 'version.py')
    s = open(p, encoding='utf-8').read()
    s = re.sub(r"VERSION = '[^']*'", f"VERSION = '{v}'", s)
    open(p, 'w', encoding='utf-8').write(s)


def build():
    os.makedirs(DIST, exist_ok=True)
    up = os.path.join(DIST, 'update.zip')
    with zipfile.ZipFile(up, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in walk(os.path.join(PROG, 'app')):
            z.write(f, os.path.relpath(f, PROG).replace(os.sep, '/'))
        for f in UPDATE_ROOT_FILES:
            p = os.path.join(PROG, f)
            if os.path.isfile(p):
                z.write(p, f)
    full = os.path.join(DIST, 'RangchaelHelper.zip')
    with zipfile.ZipFile(full, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in walk(PROG):
            z.write(f, os.path.relpath(f, HERE).replace(os.sep, '/'))
    return up, full


def gh(*args, input_file=None):
    cmd = ['gh', 'api'] + list(args)
    if input_file:
        cmd += ['--input', input_file]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f'gh 실패: {" ".join(cmd)}\n{out.stderr or out.stdout}')
    return json.loads(out.stdout or '{}')


def publish(v, notes, up, full):
    body = '\n'.join(f'- {n}' for n in notes) if notes else f'v{v}'
    rel = gh('-X', 'POST', f'repos/{REPO}/releases', '-f', f'tag_name=v{v}', '-f', 'target_commitish=main',
             '-f', f'name=v{v}', '-f', f'body={body}')
    rid = rel['id']
    for path, name in ((up, 'update.zip'), (full, 'RangchaelHelper.zip')):
        gh('-X', 'POST', '-H', 'Content-Type: application/zip',
           f'https://uploads.github.com/repos/{REPO}/releases/{rid}/assets?name={name}', input_file=path)
    return rel['html_url']


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--dry']
    if not args:
        raise SystemExit(__doc__)
    v, notes = args[0].lstrip('v'), args[1:]
    set_version(v)
    up, full = build()
    print(f'update.zip {os.path.getsize(up) / 1e6:.1f}MB / RangchaelHelper.zip {os.path.getsize(full) / 1e6:.1f}MB')
    if '--dry' in sys.argv:
        print('(--dry: 올리지 않음)')
    else:
        print('릴리스:', publish(v, notes, up, full))

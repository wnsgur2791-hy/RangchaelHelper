# -*- coding: utf-8 -*-
"""새 버전 배포 (GitHub '릴리스' 기능 없이, 저장소 파일만으로)

사용:  python tools/release.py 1.0.1 "바뀐 내용 한 줄" ["두 번째 줄" ...]

하는 일
1. 랭챌분석기/app/version.py 의 VERSION 을 바꾸고 커밋 + push (main)
2. 그 커밋 번호(ref)와 파일 목록(경로 -> sha256)을 latest.json 에 적고 커밋 + push
   사용자 PC의 app/updater.py 는 켤 때 latest.json 을 읽고, 자기 파일과 다른 것만
   raw.githubusercontent.com/<저장소>/<ref>/랭챌분석기/<경로> 에서 받아서 바꿈.
처음 받는 사람은 저장소 첫 화면의 'Code -> Download ZIP' (또는 README 링크)로 전체를 받음.
"""
import os, re, sys, json, hashlib, subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(HERE, '랭챌분석기')
SKIP_DIRS = {'__pycache__', '기록데이터', '진단', '학습대기', '클로드작업', '업데이트백업'}
ROOT_FILES = ('설명서.txt', '처음 읽어주세요.txt', 'requirements.txt')
ATTR = ('\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n'
        'Claude-Session: https://claude.ai/code/session_01RSfLpposDiHKjCGfnRBw13')


def git(*a):
    out = subprocess.run(['git', '-C', HERE] + list(a), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f'git {" ".join(a)} 실패:\n{out.stderr or out.stdout}')
    return out.stdout.strip()


def commit_push(msg):
    git('add', '-A')
    if git('status', '--porcelain'):
        git('-c', 'user.name=wnsgur2791-hy', '-c', 'user.email=wnsgur2791-hy@users.noreply.github.com',
            'commit', '-q', '-m', msg + ATTR)
    git('push', 'origin', 'HEAD:main')
    return git('rev-parse', 'HEAD')


def manifest():
    files = {}
    app = os.path.join(PROG, 'app')
    for root, dirs, fs in os.walk(app):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in fs:
            if f.endswith('.pyc') or f.endswith('.tmp'):
                continue
            p = os.path.join(root, f)
            files[os.path.relpath(p, PROG).replace(os.sep, '/')] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    for f in ROOT_FILES:
        p = os.path.join(PROG, f)
        if os.path.isfile(p):
            files[f] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return dict(sorted(files.items()))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    v, notes = sys.argv[1].lstrip('vV'), sys.argv[2:]
    p = os.path.join(PROG, 'app', 'version.py')
    s = open(p, encoding='utf-8').read()
    open(p, 'w', encoding='utf-8').write(re.sub(r"VERSION = '[^']*'", f"VERSION = '{v}'", s))
    ref = commit_push(f'랭챌 분석기 v{v}\n\n' + '\n'.join(f'- {n}' for n in notes))
    latest = {'version': v, 'ref': ref, 'notes': notes, 'files': manifest()}
    json.dump(latest, open(os.path.join(HERE, 'latest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    commit_push(f'latest.json -> v{v}')
    print(f'v{v} 배포 완료 (ref {ref[:8]}, 파일 {len(latest["files"])}개)')

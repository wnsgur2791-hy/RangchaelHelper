# -*- coding: utf-8 -*-
"""Claude Code 연동
PC에 설치된 Claude Code(claude 명령)를 '질문 1번 → 답 1번' 방식(-p 모드)으로 불러서
- 학습대기 그림들의 정답 붙이기 (일괄 학습)
- 확실하지 않게 읽힌 값(스탯, 발사각, 스킬 등) 즉석 확인
- 화면이 막혔을 때 지금 화면이 무엇인지 보고, 어디를 눌러야 하는지 알려주기
를 맡깁니다. Claude 구독(Pro/Max) 사용량을 씁니다.
"""
import os, sys, json, re, shutil, subprocess, time, uuid

import storage as ST
import score as SC

WORK_DIR = os.path.join(ST.ROOT_DIR, '클로드작업')

GAME_INTRO = ('너는 모바일 야구 게임 "컴투스프로야구V26"의 화면을 읽어 주는 도우미야. '
              '게임은 LDPlayer 에뮬레이터(세로 화면)에서 돌아가고, 내 프로그램이 화면 캡처를 보내 줄 거야. '
              '그림 파일은 지금 작업 폴더에 있으니 Read 도구로 직접 열어서 봐. 파일을 만들거나 고치지 말고, '
              '명령도 실행하지 마. 마지막 답에는 다른 말 없이 요청한 JSON 하나만 적어.\n')


class ClaudeError(Exception):
    pass


def _candidates():
    home = os.path.expanduser('~')
    out = []
    p = SC.SET.get('claude_경로') or ''
    if p:
        out.append(p)
    for name in ('claude', 'claude.exe', 'claude.cmd'):
        w = shutil.which(name)
        if w:
            out.append(w)
    out += [os.path.join(home, '.local', 'bin', 'claude.exe'),
            os.path.join(home, '.local', 'bin', 'claude'),
            os.path.join(os.environ.get('APPDATA', ''), 'npm', 'claude.cmd'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Links', 'claude.exe')]
    seen = []
    for c in out:
        if c and c not in seen and os.path.isfile(c):
            seen.append(c)
    return seen


def find_claude():
    c = _candidates()
    return c[0] if c else None


def extract_json(text):
    """답 글에서 JSON 부분만 꺼냄 (```json ... ``` 이나 앞뒤 설명이 붙어 있어도 됨)"""
    if text is None:
        return None
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
    cands = [m.group(1)] if m else []
    s = text.find('{')
    e = text.rfind('}')
    if s >= 0 and e > s:
        cands.append(text[s:e + 1])
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


class ClaudeLink:
    def __init__(self, log=print, exe=None):
        self.log = log
        self.exe = exe or find_claude()
        self.model = SC.SET.get('claude_모델') or ''
        self.timeout = float(SC.SET.get('claude_최대대기초', 300))
        self.calls = 0
        self.proc = None
        os.makedirs(WORK_DIR, exist_ok=True)
        self.cleanup(48)                       # 이틀 지난 작업 그림은 지움

    # ------------------------------------------------------------ 기본 호출
    def _cmd(self, extra):
        exe = self.exe
        if not exe:
            raise ClaudeError('Claude Code(claude)를 찾지 못했어요. 설명서 8번의 설치 방법을 확인해 주세요.')
        base = [exe]
        if exe.lower().endswith(('.cmd', '.bat')):
            base = ['cmd', '/c', exe]
        return base + extra

    def version(self):
        out = subprocess.run(self._cmd(['--version']), capture_output=True, timeout=60,
                             creationflags=_NO_WINDOW)
        return (out.stdout or b'').decode('utf-8', 'replace').strip()

    def ask(self, prompt, timeout=None):
        """prompt를 보내고 Claude의 마지막 답 글을 돌려줌. 그림은 WORK_DIR 안에 두고 파일 이름으로 알려줌"""
        args = ['-p', 'Follow the instructions given on stdin.', '--output-format', 'json',
                '--allowedTools', 'Read', '--permission-mode', 'dontAsk']
        if self.model:
            args += ['--model', self.model]
        t0 = time.time()
        env = dict(os.environ)
        for k in ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_REMOTE_SESSION_ID', 'CLAUDECODE'):
            env.pop(k, None)                       # 혹시 Claude Code 안에서 실행해도 별도 대화로 시작
        try:
            self.proc = subprocess.Popen(self._cmd(args), cwd=WORK_DIR, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                         creationflags=_NO_WINDOW)
            out, err = self.proc.communicate(GAME_INTRO.encode('utf-8') + prompt.encode('utf-8'),
                                             timeout=timeout or self.timeout)
        except FileNotFoundError:
            raise ClaudeError('Claude Code(claude)를 실행하지 못했어요. 설치가 끝났는지 확인해 주세요.')
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise ClaudeError(f'Claude가 {int(timeout or self.timeout)}초 안에 답하지 않았어요.')
        finally:
            rc = self.proc.returncode if self.proc else None
            self.proc = None
        self.calls += 1
        txt = (out or b'').decode('utf-8', 'replace').strip()
        res = None
        for line in [txt] + txt.splitlines()[::-1]:
            try:
                res = json.loads(line)
                break
            except ValueError:
                continue
        if not isinstance(res, dict):
            e = (err or b'').decode('utf-8', 'replace').strip()
            raise ClaudeError('Claude 응답을 이해하지 못했어요: ' + (e or txt)[:300])
        result = res.get('result') or ''
        if res.get('is_error'):
            low = result.lower()
            if 'login' in low or 'auth' in low or 'credential' in low:
                raise ClaudeError('Claude Code 로그인이 필요해요. 설명서 8번대로 claude를 한 번 실행해서 로그인해 주세요.')
            raise ClaudeError('Claude 오류: ' + result[:300])
        self.last_secs = time.time() - t0
        return result

    def ask_json(self, prompt, timeout=None):
        text = self.ask(prompt, timeout)
        data = extract_json(text)
        if data is None:
            raise ClaudeError('Claude 답에서 JSON을 찾지 못했어요: ' + text[:200])
        return data

    def stop(self):
        p = self.proc
        if p:
            try:
                p.kill()
            except Exception:
                pass

    def check(self):
        """연결 확인: 설치 + 로그인"""
        v = self.version()
        ans = self.ask('연결 확인이야. {"ok": true} 라고만 답해.', timeout=120)
        ok = isinstance(extract_json(ans), dict)
        return v, ok

    # ------------------------------------------------------------ 그림 저장 도우미
    @staticmethod
    def save_image(img, tag):
        name = f'{time.strftime("%m%d_%H%M%S")}_{tag}_{uuid.uuid4().hex[:4]}.png'
        img.save(os.path.join(WORK_DIR, name))
        return name

    @staticmethod
    def cleanup(older_than_hours=24):
        """오래된 작업 그림 정리"""
        now = time.time()
        for fn in os.listdir(WORK_DIR) if os.path.isdir(WORK_DIR) else []:
            p = os.path.join(WORK_DIR, fn)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) > older_than_hours * 3600:
                    os.remove(p)
            except OSError:
                pass


_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

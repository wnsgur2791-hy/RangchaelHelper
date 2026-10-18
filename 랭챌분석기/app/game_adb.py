# -*- coding: utf-8 -*-
"""에뮬레이터(LDPlayer) 조작 - ADB 방식

- 화면: adb screencap 으로 에뮬레이터 안쪽 화면을 바로 받아옴 (창이 가려져 있어도 됨)
- 클릭: adb shell input tap 으로 안드로이드에 터치를 보냄 (PC 마우스는 움직이지 않음)
"""
import os, sys, time, shutil, subprocess, threading
import numpy as np
from PIL import Image

from regions import REF_W, REF_H
from game import StopRequested

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def _ldplayer_dirs():
    """LDPlayer 설치 폴더 후보"""
    dirs = []
    if os.name == 'nt':
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for key in (r'Software\XuanZhi\LDPlayer9', r'Software\XuanZhi\LDPlayer',
                            r'Software\XuanZhi\LDPlayer64', r'Software\ChangZhi\LDPlayer'):
                    try:
                        with winreg.OpenKey(root, key) as k:
                            v, _ = winreg.QueryValueEx(k, 'InstallDir')
                            dirs.append(v)
                    except OSError:
                        pass
        except ImportError:
            pass
        for drive in ('C:', 'D:', 'E:'):
            for sub in (r'\LDPlayer\LDPlayer9', r'\LDPlayer\LDPlayer4.0', r'\LDPlayer9', r'\LDPlayer',
                        r'\Program Files\LDPlayer\LDPlayer9', r'\Program Files\LDPlayer9'):
                dirs.append(drive + sub)
        # 실행 중인 LDPlayer 위치
        try:
            out = subprocess.run(['powershell', '-NoProfile', '-Command',
                                  "Get-Process dnplayer,ldplayer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path"],
                                 capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW).stdout
            for line in out.splitlines():
                if line.strip():
                    dirs.insert(0, os.path.dirname(line.strip()))
        except Exception:
            pass
    return dirs


def find_adb(configured=None):
    cands = [configured] if configured else []
    for d in _ldplayer_dirs():
        cands.append(os.path.join(d, 'adb.exe'))
    cands.append(shutil.which('adb'))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


class AdbGame:
    def __init__(self, adb_path=None, device=None, log=print):
        self.adb = find_adb(adb_path)
        if not self.adb:
            raise RuntimeError('adb.exe를 찾지 못했어요. LDPlayer가 설치되어 있는지 확인하고, '
                               '안 되면 설정.json에 "adb_경로"를 적어 주세요. (설명서 참고)')
        self.want_device = device
        self.device = None
        self.size = None
        self.stop_flag = False
        self.log = log
        self.lock = threading.Lock()

    # ---------------------------------------------------------- 연결
    def _run(self, args, timeout=15, binary=False):
        cmd = [self.adb] + (['-s', self.device] if self.device else []) + args
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout if binary else r.stdout.decode('utf-8', 'ignore')

    def _devices(self):
        out = subprocess.run([self.adb, 'devices'], capture_output=True, timeout=15,
                             creationflags=NO_WINDOW).stdout.decode('utf-8', 'ignore')
        return [l.split('\t')[0] for l in out.splitlines()[1:] if l.strip().endswith('\tdevice')]

    def find(self):
        if self.device and self.device in self._devices():
            return self.device
        devs = self._devices()
        if not devs:
            for port in (5555, 5557, 5559):
                subprocess.run([self.adb, 'connect', f'127.0.0.1:{port}'], capture_output=True, timeout=10,
                               creationflags=NO_WINDOW)
            devs = self._devices()
        if self.want_device:
            if self.want_device not in devs:
                subprocess.run([self.adb, 'connect', self.want_device], capture_output=True, timeout=10,
                               creationflags=NO_WINDOW)
                devs = self._devices()
            devs = [d for d in devs if d == self.want_device] or devs
        if not devs:
            raise RuntimeError('에뮬레이터에 연결하지 못했어요. LDPlayer가 켜져 있는지, '
                               'LDPlayer 설정 → 기타 설정 → "ADB 디버깅"이 "로컬 연결 열기"인지 확인해 주세요.')
        # 에뮬레이터 여러 개면 첫 번째 사용
        self.device = devs[0]
        self.size = None
        return self.device

    def screen_size(self):
        if self.size is None:
            out = self._run(['shell', 'wm', 'size'])
            w = h = None
            for line in out.splitlines():
                if 'size' in line:
                    wh = line.split(':')[-1].strip()
                    w, h = (int(v) for v in wh.split('x'))
            if not w:
                raise RuntimeError('에뮬레이터 화면 크기를 알 수 없어요.')
            self.size = (w, h)
        return self.size

    def client_rect(self):
        w, h = self.screen_size()
        return 0, 0, w, h

    def activate(self):
        pass

    def elevation_info(self):
        return None, None

    # ---------------------------------------------------------- 캡처 / 터치
    def grab(self):
        self.check_stop()
        data = None
        for attempt in range(3):
            try:
                data = self._run(['exec-out', 'screencap'], timeout=15, binary=True)
                if data and len(data) > 16:
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(0.3)
        if not data or len(data) <= 16:
            raise RuntimeError('에뮬레이터 화면을 가져오지 못했어요. LDPlayer가 켜져 있는지 확인해 주세요.')
        w = int.from_bytes(data[0:4], 'little')
        h = int.from_bytes(data[4:8], 'little')
        px = w * h * 4
        if w <= 0 or h <= 0 or len(data) < px + 12:
            # 일부 환경은 raw를 못 줌 -> PNG로 다시
            import io
            png = self._run(['exec-out', 'screencap', '-p'], timeout=20, binary=True)
            img = Image.open(io.BytesIO(png)).convert('RGB')
        else:
            raw = data[len(data) - px:]
            img = Image.frombuffer('RGBA', (w, h), raw, 'raw', 'RGBA', 0, 1).convert('RGB')
        if img.width > img.height:
            raise RuntimeError('에뮬레이터가 가로 화면이에요. LDPlayer 설정 → 해상도를 "휴대폰 900x1600"(세로)로 바꿔 주세요.')
        if abs((img.width / img.height) / (REF_W / REF_H) - 1) > 0.04:
            raise RuntimeError(f'에뮬레이터 화면 비율이 달라요 (지금 {img.width}x{img.height}). '
                               'LDPlayer 설정 → 해상도를 900x1600(세로)로 바꿔 주세요.')
        self.size = img.size
        if img.size != (REF_W, REF_H):
            img = img.resize((REF_W, REF_H), Image.LANCZOS)
        return img

    def screen_point(self, pt):
        w, h = self.screen_size()
        x, y = int(pt[0] * w / REF_W), int(pt[1] * h / REF_H)
        return x, y, x, y

    def click(self, pt, method=None):
        self.check_stop()
        x, y, _, _ = self.screen_point(pt)
        self._run(['shell', 'input', 'tap', str(x), str(y)], timeout=10)

    # ---------------------------------------------------------- 중지
    def check_stop(self):
        if os.name == 'nt':
            try:
                import ctypes
                if ctypes.windll.user32.GetAsyncKeyState(0x7B) & 0x8000:     # F12
                    self.stop_flag = True
            except Exception:
                pass
        if self.stop_flag:
            raise StopRequested()

    def sleep(self, s):
        end = time.time() + s
        while time.time() < end:
            self.check_stop()
            time.sleep(min(0.05, max(0, end - time.time())))

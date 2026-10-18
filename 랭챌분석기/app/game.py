# -*- coding: utf-8 -*-
"""게임 창 찾기 / 화면 캡처 / 클릭 (Windows 전용)

- 게임 창 제목에 '컴프야V26'이 들어간 창을 찾습니다.
- 창 안쪽(제목줄 제외) 화면을 캡처해서 기준 크기(918x1633)로 맞춥니다.
- 클릭은 실제 마우스를 그 위치로 옮겨서 누릅니다. (작업 중에는 마우스를 건드리지 마세요)
"""
import time, ctypes, sys, threading
import numpy as np
from PIL import Image

from regions import REF_W, REF_H

WINDOW_TITLE = '컴프야V26'


class StopRequested(Exception):
    pass


def set_dpi_aware():
    """윈도우 배율(125%, 150% 등) 때문에 좌표가 어긋나지 않도록. 프로그램 창을 띄우기 전에 불러야 함"""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class WindowsGame:
    def __init__(self, title=WINDOW_TITLE):
        if sys.platform != 'win32':
            raise RuntimeError('이 프로그램은 Windows에서만 게임을 조작할 수 있어요.')
        set_dpi_aware()
        import win32gui
        self.win32gui = win32gui
        self._local = threading.local()     # 캡처 도구는 작업 흐름(스레드)마다 따로 만들어야 함
        self.title = title
        self.hwnd = None
        self.stop_flag = False

    # ---------------------------------------------------------- 창
    def find(self):
        found = []

        def cb(h, _):
            if self.win32gui.IsWindowVisible(h) and self.title in self.win32gui.GetWindowText(h):
                found.append(h)
        self.win32gui.EnumWindows(cb, None)
        if not found:
            raise RuntimeError(f"'{self.title}' 창을 찾을 수 없어요. 게임(크로스 플랫폼)을 창모드로 켜 주세요.")
        exact = [h for h in found if self.win32gui.GetWindowText(h).strip() == self.title]
        if not exact:
            # 브라우저 탭 등 제목에 같은 글자가 들어간 다른 창 제외
            exact = [h for h in found if not any(b in self.win32gui.GetWindowText(h)
                                                for b in ('Chrome', 'Edge', 'Whale', 'Firefox', '갤러리'))]
        if not exact:
            raise RuntimeError(f"'{self.title}' 게임 창을 찾을 수 없어요. 게임(크로스 플랫폼)을 창모드로 켜 주세요.")
        self.hwnd = exact[0]
        return self.hwnd

    def client_rect(self):
        if self.hwnd is None or not self.win32gui.IsWindow(self.hwnd):
            self.find()
        l, t, r, b = self.win32gui.GetClientRect(self.hwnd)
        x, y = self.win32gui.ClientToScreen(self.hwnd, (0, 0))
        return x, y, r - l, b - t

    def activate(self):
        try:
            if self.win32gui.IsIconic(self.hwnd):
                self.win32gui.ShowWindow(self.hwnd, 9)
            self.win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        time.sleep(0.2)

    # ---------------------------------------------------------- 캡처 / 클릭
    def grab(self):
        self.check_stop()
        x, y, w, h = self.client_rect()
        if w < 200 or h < 300:
            raise RuntimeError('게임 창이 너무 작거나 최소화되어 있어요.')
        if abs((w / h) / (REF_W / REF_H) - 1) > 0.04:
            raise RuntimeError(f'게임 창 비율이 달라요 (지금 {w}x{h}). 1920x1080 해상도 창모드에서 창 크기를 바꾸지 말고 써 주세요.')
        img = self._capture(x, y, w, h)
        if img.size != (REF_W, REF_H):
            img = img.resize((REF_W, REF_H), Image.LANCZOS)
        return img

    def _mss(self):
        sct = getattr(self._local, 'sct', None)
        if sct is None:
            import mss
            sct = mss.mss()
            self._local.sct = sct
        return sct

    def _capture(self, x, y, w, h):
        """화면 캡처: mss -> 실패하면 PIL ImageGrab -> 잠깐 쉬고 다시 (최대 4번)"""
        last = None
        for attempt in range(4):
            try:
                shot = self._mss().grab({'left': x, 'top': y, 'width': w, 'height': h})
                return Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
            except Exception as e:
                last = e
                try:
                    self._local.sct.close()
                except Exception:
                    pass
                self._local.sct = None
            try:
                from PIL import ImageGrab
                return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert('RGB')
            except Exception as e:
                last = e
            time.sleep(0.5)
        raise RuntimeError('화면을 캡처하지 못했어요. 잠금 화면이나 관리자 권한 확인 창이 떠 있지 않은지 확인하고, '
                           '그래도 안 되면 실행.bat을 오른쪽 클릭 → [관리자 권한으로 실행] 해 주세요. (' + str(last) + ')')

    # 클릭 방식
    #  'drag'    = 실제 마우스처럼 커서를 조금씩 끌어서 옮긴 뒤 클릭 (순간이동 안 함)
    #  'input'   = 커서를 순간이동 + 마우스 입력(SendInput)
    #  'message' = 게임 창에 클릭 메시지를 직접 보냄
    METHODS = ('drag', 'input', 'message')

    def screen_point(self, pt):
        x, y, w, h = self.client_rect()
        return int(x + pt[0] * w / REF_W), int(y + pt[1] * h / REF_H), int(pt[0] * w / REF_W), int(pt[1] * h / REF_H)

    def click(self, pt, method=None):
        self.check_stop()
        method = method or getattr(self, 'prefer', self.METHODS[0])
        sx, sy, cx, cy = self.screen_point(pt)
        if method == 'message':
            self._click_message(cx, cy)
            self.trace(method, sx, sy, '-')
        elif method == 'drag':
            self._click_drag(sx, sy)
        else:
            self._click_input(sx, sy)
        self.last_method = method

    # ---- 진단 기록 (클릭 실패 시 화면에 보여 줌)
    def trace(self, method, sx, sy, info):
        self.traces = (getattr(self, 'traces', []) + [f'{method}: 목표({sx},{sy}) {info}'])[-6:]

    @staticmethod
    def cursor():
        p = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def _click_drag(self, sx, sy):
        """상대 이동(SendInput MOVE)으로 목표까지 조금씩 이동 -> 위치 확인 -> 누르고 떼기"""
        path = []
        for _ in range(25):
            cx, cy = self.cursor()
            dx, dy = sx - cx, sy - cy
            path.append((cx, cy))
            if abs(dx) <= 1 and abs(dy) <= 1:
                break
            step = 1 if max(abs(dx), abs(dy)) < 40 else 0.5      # 멀면 절반씩, 가까우면 남은 만큼
            _send_mouse(int(round(dx * step)) or (1 if dx > 0 else -1 if dx < 0 else 0),
                        int(round(dy * step)) or (1 if dy > 0 else -1 if dy < 0 else 0), 0x0001)
            time.sleep(0.015)
        time.sleep(0.05)
        end = self.cursor()
        _send_mouse(0, 0, 0x0002)
        time.sleep(0.09)
        _send_mouse(0, 0, 0x0004)
        self.trace('drag', sx, sy, f'시작{path[0]} → 끝{end} ({len(path)}번 이동)')

    def _click_input(self, sx, sy):
        u = ctypes.windll.user32
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)          # 가상 화면 왼쪽 위
        vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)          # 가상 화면 크기
        ax = int((sx - vx) * 65535 / max(1, vw - 1))
        ay = int((sy - vy) * 65535 / max(1, vh - 1))
        MOVE, ABS, VDESK, DOWN, UP = 0x0001, 0x8000, 0x4000, 0x0002, 0x0004
        ok = u.SetCursorPos(sx, sy)
        err = ctypes.GetLastError() if not ok else 0
        a1 = self.cursor()
        _send_mouse(ax, ay, MOVE | ABS | VDESK)
        time.sleep(0.04)
        a2 = self.cursor()
        _send_mouse(ax, ay, MOVE | ABS | VDESK)
        time.sleep(0.04)
        _send_mouse(ax, ay, DOWN | ABS | VDESK)
        time.sleep(0.09)
        _send_mouse(ax, ay, UP | ABS | VDESK)
        self.cursor_check = (sx, sy, a1[0], a1[1])
        self.trace('input', sx, sy, f'SetCursorPos={ok}{"(오류" + str(err) + ")" if err else ""} 직후{a1} 이동후{a2} '
                                    f'가상화면({vx},{vy},{vw}x{vh})')

    def _click_message(self, cx, cy):
        """게임 창(과 그 안쪽 창)에 클릭 메시지를 직접 보냄. 실패해도 멈추지 않음"""
        u = ctypes.windll.user32
        lp = (cy << 16) | (cx & 0xFFFF)
        targets = [self.hwnd]
        child = self._child_at(cx, cy)
        if child and child != self.hwnd:
            targets.insert(0, child)
        for t in targets:
            u.PostMessageW(t, 0x0200, 0, lp)          # WM_MOUSEMOVE
            time.sleep(0.03)
            u.PostMessageW(t, 0x0201, 0x0001, lp)     # WM_LBUTTONDOWN
            time.sleep(0.09)
            u.PostMessageW(t, 0x0202, 0, lp)          # WM_LBUTTONUP

    def _child_at(self, cx, cy):
        try:
            h = self.win32gui.ChildWindowFromPoint(self.hwnd, (cx, cy))
            return h or self.hwnd
        except Exception:
            return self.hwnd

    # ---------------------------------------------------------- 관리자 권한 확인
    def elevation_info(self):
        try:
            me = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            me = None
        w = self.elevation_warning() if not me else None
        return me, w

    def elevation_warning(self):
        """게임이 관리자 권한인데 이 프로그램은 아니면, 윈도우가 클릭을 막음"""
        try:
            me_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            if me_admin:
                return None
            import win32process
            _, pid = win32process.GetWindowThreadProcessId(self.hwnd)
            k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
            hp = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not hp:
                return '게임이 관리자 권한으로 실행 중인 것 같아요.'
            tok = ctypes.c_void_p()
            if not adv.OpenProcessToken(hp, 0x0008, ctypes.byref(tok)):   # TOKEN_QUERY
                k32.CloseHandle(hp)
                return '게임이 관리자 권한으로 실행 중인 것 같아요.'
            elev = ctypes.c_uint32()
            size = ctypes.c_uint32()
            adv.GetTokenInformation(tok, 20, ctypes.byref(elev), 4, ctypes.byref(size))   # TokenElevation
            k32.CloseHandle(tok)
            k32.CloseHandle(hp)
            if elev.value:
                return '게임이 관리자 권한으로 실행 중이에요.'
        except Exception:
            return None
        return None

    def check_stop(self):
        # F12를 누르면 언제든 중지
        try:
            if ctypes.windll.user32.GetAsyncKeyState(0x7B) & 0x8000:
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


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('mi', _MOUSEINPUT)]


def _send_mouse(ax, ay, flags):
    inp = _INPUT(0, _MOUSEINPUT(ax, ay, 0, flags, 0, 0))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def region_diff(a, b, box):
    x = np.asarray(a.crop(box).convert('L')).astype(np.int16)
    y = np.asarray(b.crop(box).convert('L')).astype(np.int16)
    return float(np.abs(x - y).mean())

# -*- coding: utf-8 -*-
"""자동 진행: 상대 분석 -> 추천대로 선발 배치 -> 연속 경기 -> 결과 기록 -> 갱신 -> 반복

- 갱신: 무료는 바로, 포인트/스타는 '즉시 갱신' 창에서 결제 버튼을 눌러야 함
- 멈추는 때
  · 포인트만 모드: 다음 갱신이 스타로 바뀌면, 그때 로비의 5경기는 치르지 않고 남겨 둠 (00시 레이팅 관리)
  · 스타까지 모드: 정한 스타 갱신 횟수를 다 쓰면 마찬가지로 마지막 5경기는 남겨 둠
  · 마감 시각(기본 23:59)이 가까우면 새 바퀴를 시작하지 않음
  · 모르는 화면이 나오면 멈춤 (Claude 연동 중이면 닫기/확인만 눌러 로비로 돌아오기를 시도)
- 절대 누르지 않는 것: 구매, 스타 사용(스타 모드에서 정한 횟수 외), [연속 경기 종료]
"""
import os, time, datetime, collections

import recognize as R
import storage as ST
import score as SC
from regions import AUTO, LOBBY, ORDER, order_row, my_card
from flows import FlowError

STATE = os.path.join(ST.DATA_DIR, 'auto_state.json')


# ------------------------------------------------------------ 선발 바꾸기 순서 계산
def plan_swaps(cur, target, visible=3):
    """cur/target: 줄마다 선발 번호 [3, 1, 2, 5, 4] -> 바꾸기 목록 [(먼저 누를 줄, 나중에 누를 줄), ...]
    첫 투수를 누르면 아래 창이 4·5번 줄을 가리므로, 두 번째로 누르는 줄은 항상 1~3번 줄(0~2)이어야 함.
    그 조건 안에서 가장 적게 바꾸는 방법을 찾음 (5줄이라 경우의 수가 120개뿐이라 전부 찾아봄)"""
    cur, target = tuple(cur), tuple(target)
    if sorted(cur) != sorted(target):
        raise ValueError(f'지금 배치 {cur} 와 목표 {target} 의 선발 구성이 달라요')
    n = len(cur)
    moves = [(i, j) for i in range(n) for j in range(i + 1, n) if i < visible]
    prev = {cur: None}
    q = collections.deque([cur])
    while q:
        s = q.popleft()
        if s == target:
            break
        for i, j in moves:
            t = list(s)
            t[i], t[j] = t[j], t[i]
            t = tuple(t)
            if t not in prev:
                prev[t] = (s, (i, j))
                q.append(t)
    path = []
    s = target
    while prev[s] is not None:
        s, (i, j) = prev[s]
        path.append((j, i) if j >= visible else (i, j))   # 가려지는 줄(4·5번)은 먼저 누름
    return path[::-1]


def target_from_pending(pending, cur):
    """분석 결과의 추천을 줄별 목표 배치로. 못 읽은 상대 줄은 남는 선발로 채움 (가능하면 지금 그대로)"""
    tgt = [None] * 5
    for r in pending.get('recommend', []):
        tgt[r['row'] - 1] = r['recommend']
    left = [x for x in (1, 2, 3, 4, 5) if x not in tgt]
    for i in range(5):
        if tgt[i] is None and cur[i] in left:
            tgt[i] = cur[i]
            left.remove(cur[i])
    for i in range(5):
        if tgt[i] is None:
            tgt[i] = left.pop(0)
    return tgt


# ------------------------------------------------------------ 자동 진행
class AutoRunner:
    def __init__(self, runner, log, mode='point', star_cap=0, deadline='23:59', max_cycles=0, show=None):
        self.r = runner
        self.g = runner.g
        self.log = log
        self.mode = mode                    # 'point' 또는 'star'
        self.star_cap = int(star_cap or 0)
        self.deadline = deadline
        self.max_cycles = int(max_cycles or 0)
        self.show = show                    # 분석 결과를 화면에 보여 주는 함수
        self.margin_min = float(SC.SET.get('자동_새바퀴_여유분', 12))
        self.series_timeout = float(SC.SET.get('연속경기_최대대기초', 600))

    # ---------- 상태 (오늘 쓴 스타 갱신 횟수)
    def _state(self):
        st = ST.load_json(STATE) or {}
        today = datetime.date.today().isoformat()
        if st.get('date') != today:
            st = {'date': today, 'stars_used': 0, 'points_used': 0, 'free_used': 0}
        return st

    def _save_state(self, st):
        ST.save_json(STATE, st)

    def allowed(self, kind, st):
        if kind == 'done':                     # 금일 갱신 완료: 오늘은 더 갱신할 수 없음
            return False
        if kind in ('free', 'point'):
            return True
        return self.mode == 'star' and st['stars_used'] < self.star_cap

    def minutes_left(self):
        now = datetime.datetime.now()
        h, m = (int(x) for x in self.deadline.split(':'))
        end = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return (end - now).total_seconds() / 60

    # ---------- 공통
    def grab(self):
        return self.g.grab()

    def screen(self, img=None):
        return R.which_screen(img or self.grab())

    def wait_for(self, names, timeout, every=0.5):
        names = (names,) if isinstance(names, str) else names
        end = time.time() + timeout
        while time.time() < end:
            img = self.grab()
            if R.which_screen(img) in names:
                return self.r.settle(img)
            self.g.sleep(every)
        return None

    def to_lobby(self, why):
        """로비가 아니면 돌아가 보기: 닫기 위치 -> Claude 도움 -> 실패면 멈춤"""
        img = self.grab()
        if R.which_screen(img) == 'lobby':
            return img
        path = self.r.save_diag('자동_' + why)
        self.log(f'  ⚠ 로비가 아니에요 ({why}). 화면 저장: {path}')
        if self.r.recover_to_lobby():
            return self.grab()
        if self.r.claude_rescue('랭킹 챌린지 로비 화면(상대 5명 목록과 연속 경기 버튼이 보이는 화면)으로 돌아가기',
                                why, lambda im: R.which_screen(im) == 'lobby'):
            return self.grab()
        raise FlowError('로비로 돌아오지 못해서 자동 진행을 멈췄어요. 게임 화면을 확인해 주세요.')

    # ---------- 선발 배치
    def arrange(self, pending):
        """순서 변경 화면에서 추천대로 선발을 바꾸고 저장 -> 로비"""
        img = self.r.click_until(LOBBY['order_button'], lambda im: R.which_screen(im) == 'order')
        if img is None:
            raise FlowError('[순서 변경] 화면이 열리지 않았어요.')
        cur = self.read_rotation(img)
        tgt = target_from_pending(pending, cur)
        swaps = plan_swaps(cur, tgt)
        self.log(f'  배치: 지금 {cur} → 목표 {tgt} ({len(swaps)}번 바꾸기)')
        for a, b in swaps:
            ok = False
            for attempt in range(2):
                self.g.click(my_card(a))
                if not self.wait_until(R.order_panel_open, 4):
                    continue
                self.g.click(my_card(b))
                self.g.sleep(self.r.wait_click + 0.3)
                self.g.click(AUTO['swap_btn'])
                img = self.wait_until(lambda im: R.which_screen(im) == 'order' and not R.order_panel_open(im), 4)
                if img is None:
                    continue
                img = self.r.settle(img)
                now = self.read_rotation(img)
                exp = list(cur)
                exp[a], exp[b] = exp[b], exp[a]
                if now == exp:
                    cur, ok = now, True
                    break
                self.log(f'  ⚠ {a + 1}↔{b + 1}번 줄 바꾸기 결과가 예상과 달라요 (읽은 배치 {now})')
                cur = now
                break
            if not ok:
                raise FlowError(f'{a + 1}번 줄과 {b + 1}번 줄 선발을 바꾸지 못했어요.')
        if cur != tgt:
            raise FlowError(f'배치가 목표와 달라요 (지금 {cur} / 목표 {tgt}).')
        # 저장 (바꾼 게 있으면 [저장], 없으면 X)
        btn = AUTO['save_btn'] if swaps else ORDER['close']
        img = self.r.click_until(btn, lambda im: R.which_screen(im) in ('lobby', 'save_confirm'))
        if img is not None and R.which_screen(img) == 'save_confirm':
            img = self.r.click_until(AUTO['save_ok'], lambda im: R.which_screen(im) == 'lobby')
        if img is None:
            raise FlowError('배치를 저장하고 로비로 돌아오지 못했어요.')
        # 기록용: 이 분석의 '지금 배치'를 바꾼 배치로 고쳐 둠
        for o in pending.get('opps', []):
            o['label']['my_sp'] = tgt[o['row'] - 1]
        for r in pending.get('recommend', []):
            r['current'] = tgt[r['row'] - 1]
        ST.save_json(ST.PENDING, pending)
        self.log('  ✓ 추천대로 배치하고 저장했어요.', 'good')
        return tgt

    def read_rotation(self, img):
        rows = R.read_order_labels(img)
        self.r.claude_fill_labels(img, rows, [order_row(i)['my_label'] for i in range(5)], '순서 변경')
        cur = [r.get('my_sp') for r in rows]
        if None in cur or sorted(cur) != [1, 2, 3, 4, 5]:
            raise FlowError(f'순서 변경 화면의 내 선발 번호를 다 읽지 못했어요 ({cur}).')
        return cur

    def wait_until(self, cond, timeout):
        end = time.time() + timeout
        while time.time() < end:
            self.g.sleep(0.2)
            img = self.grab()
            if cond(img):
                return img
        return None

    # ---------- 연속 경기
    def play_series(self):
        img = self.r.click_until(AUTO['cont_btn'], lambda im: R.which_screen(im) == 'cont_popup')
        if img is None:
            path = self.r.save_diag('연속경기창')
            raise FlowError(f'[연속 경기]를 눌러도 경기 목록 창이 안 떴어요. (화면 저장: {path})')
        img = self.r.click_until(AUTO['cont_go'], lambda im: R.which_screen(im) != 'cont_popup', tries=3)
        if img is None:
            raise FlowError('[경기 진행]이 눌리지 않았어요.')
        self.log('  연속 경기 진행 중... (보통 5분)')
        t0 = time.time()
        last = 0
        while time.time() - t0 < self.series_timeout:
            self.g.sleep(3)
            img = self.grab()
            scr = R.which_screen(img)
            if scr == 'series_result':
                break
            if scr == 'lobby' and time.time() - t0 > 20:
                self.log('  (연속 경기 결과 창 없이 로비로 돌아왔어요)')
                return self.grab()
            if time.time() - last > 60:
                last = time.time()
                self.log(f'  ... {int(time.time() - t0) // 60}분 경과')
        else:
            path = self.r.save_diag('연속경기_시간초과')
            raise FlowError(f'연속 경기가 {int(self.series_timeout // 60)}분 안에 끝나지 않았어요. 화면 저장: {path}')
        self.log(f'  연속 경기 끝 ({(time.time() - t0) / 60:.1f}분)')
        self.g.sleep(1.0)
        img = self.r.click_until(AUTO['series_ok'], lambda im: R.which_screen(im) != 'series_result', tries=3)
        # 승급 화면 등: 로비가 안 나오면 화면 가운데를 한 번 눌러 넘김
        for _ in range(3):
            got = self.wait_for('lobby', 4)
            if got is not None:
                return got
            self.log('  (로비가 아닌 화면 → 화면 가운데를 한 번 눌러요. 승급 화면일 수 있어요)')
            self.r.save_diag('연속경기후_화면')
            self.g.click(AUTO['center'])
            self.g.sleep(1.5)
        return self.to_lobby('연속 경기 뒤')

    # ---------- 갱신
    def do_refresh(self, kind, st):
        self.log(f"  갱신: {'무료' if kind == 'free' else ('포인트' if kind == 'point' else '스타')}")
        before = self.grab()
        self.g.click(AUTO['refresh_btn'])
        if kind == 'free':
            img = self.wait_until(lambda im: R.which_screen(im) == 'lobby' and not any(r['result'] for r in R.read_lobby(im)), 10)
            if img is None:
                raise FlowError('무료 갱신 뒤 새 상대 목록이 나오지 않았어요.')
        else:
            img = self.wait_for('refresh_popup', 6)
            if img is None:
                raise FlowError('갱신 버튼을 눌러도 즉시 갱신 창이 안 떴어요.')
            pk = R.refresh_popup_kind(img)
            if pk == 'star' and not self.allowed('star', st):
                self.g.click(AUTO['refresh_close'])                       # 창 닫기 (X)
                raise FlowError('즉시 갱신 창에 스타가 표시돼서 누르지 않고 멈췄어요.')
            self.g.click(AUTO['refresh_pay'])
            img = self.wait_until(lambda im: R.which_screen(im) == 'lobby' and not any(r['result'] for r in R.read_lobby(im)), 12)
            if img is None:
                raise FlowError('갱신 뒤 새 상대 목록이 나오지 않았어요.')
            kind = pk
        key = {'free': 'free_used', 'point': 'points_used', 'star': 'stars_used'}[kind]
        st[key] = st.get(key, 0) + 1
        self._save_state(st)
        return self.r.settle(img)

    # ---------- 한 바퀴씩
    def run(self):
        st = self._state()
        self.log(f"자동 진행 시작: {'포인트 갱신까지' if self.mode == 'point' else f'스타 갱신 최대 {self.star_cap}번까지'}"
                 f" / 마감 {self.deadline}" + (f' / 최대 {self.max_cycles}바퀴' if self.max_cycles else ''), 'head')
        self.log(f"  오늘 쓴 갱신: 무료 {st.get('free_used', 0)} / 포인트 {st.get('points_used', 0)} / 스타 {st.get('stars_used', 0)}")
        cycles = 0
        while True:
            img = self.to_lobby('시작')
            lobby = R.read_lobby(img)
            played = [bool(r['result']) for r in lobby]
            if any(played):
                out = self.r.record_results(img)
                if out['recorded']:
                    self.log('  ' + out['msg'], 'good')
            if all(played):
                kind = R.refresh_kind(img)
                if kind == 'done':
                    return self.finish('오늘 갱신을 모두 썼어요 (금일 갱신 완료)')
                if not self.allowed(kind, st):
                    return self.finish('갱신할 수 있는 횟수를 다 썼어요 (다음 갱신은 스타)')
                if self.minutes_left() < self.margin_min:
                    return self.finish(f'마감 시각({self.deadline})이 가까워서 멈췄어요')
                img = self.do_refresh(kind, st)
            # 새 목록: 다음 갱신이 막혀 있으면 이 5경기는 남겨 둠
            lobby = R.read_lobby(img)
            fresh = not any(r['result'] for r in lobby)
            nk = R.refresh_kind(img)
            if fresh and nk == 'done':
                return self.finish('오늘 갱신을 모두 써서(금일 갱신 완료) 지금 5경기는 다음 날을 위해 남겨 두고 멈췄어요')
            if fresh and not self.allowed(nk, st):
                return self.finish('다음 갱신이 스타라서 지금 5경기는 다음 날을 위해 남겨 두고 멈췄어요')
            if self.max_cycles and cycles >= self.max_cycles:
                return self.finish(f'정한 {self.max_cycles}바퀴를 다 돌았어요')
            if self.minutes_left() < self.margin_min:
                return self.finish(f'마감 시각({self.deadline})이 가까워서 새 경기를 시작하지 않았어요')
            cycles += 1
            self.log('')
            self.log(f'■ {cycles}바퀴째', 'head')
            if fresh:
                res = self.r.analyze()
                if res.get('has_results'):
                    continue
                if self.show:
                    self.show(res)
                self.to_lobby('분석 뒤')
                self.arrange(res['pending'])
            else:
                self.log('  이미 일부 경기를 치른 목록이라 남은 경기만 진행해요 (배치는 그대로).')
            img = self.play_series()
            out = self.r.record_results(img)
            self.log('  ' + out['msg'], 'good' if out['recorded'] else None)
            if out.get('excel_error'):
                self.log('  ⚠ ' + out['excel_error'])

    def finish(self, why):
        st = self._state()
        self.log('')
        self.log(f'■ 자동 진행 끝: {why}', 'head')
        self.log(f"  오늘 쓴 갱신: 무료 {st.get('free_used', 0)} / 포인트 {st.get('points_used', 0)} / 스타 {st.get('stars_used', 0)}")
        return why

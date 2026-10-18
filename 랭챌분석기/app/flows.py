# -*- coding: utf-8 -*-
"""자동 순회 흐름
1) 내 데이터 갱신하기 : 라인업 화면 -> 타자 9명(DH부터) + 선발 5명
2) 상대 분석         : 랭챌 로비 -> [순서 변경] -> 줄마다 CLICK! -> 상대 선발 카드 -> 선발+타자 9명
3) 결과 기록         : 랭챌 로비의 승/패 결과를 직전 분석과 짝지어 기록
"""
import os, time, copy, difflib
import recognize as R
import score as SC
import storage as ST
from regions import DETAIL, P1, P2, LOBBY, ORDER, LINEUP, order_row, lobby_row
from game import region_diff, StopRequested

BAT_POS_ORDER = ['DH', 'C', '1B', '2B', '3B', 'SS', 'LF', 'CF', 'RF']


class FlowError(Exception):
    pass


class Runner:
    def __init__(self, game, log, helper=None):
        self.g = game
        self.log = log
        self.helper = helper            # Claude Code 연동 (없으면 None)
        self.check_list = []            # Claude에게 확인받을 선수 [(선수, 화면들, 위치, 학습대기)]
        self.validate = True            # 타자 목록 검사 (테스트용 가짜 화면에서만 끔)
        self.claude_now = SC.SET.get('claude_확인방식', '바로') != '한꺼번에'   # 바로 = 카드마다 그 자리에서 확인
        self.sn = R.SkillNames(SC.skill_names('타자'), SC.skill_names('투수'))
        self.wait_click = SC.SET.get('클릭_후_대기초', 0.6)
        self.timeout = SC.SET.get('화면전환_최대대기초', 6.0)

    # ------------------------------------------------------------ 기다리기
    def wait_screen(self, name, timeout=None, click_again=None):
        end = time.time() + (timeout or self.timeout)
        tried = False
        while time.time() < end:
            img = self.g.grab()
            if R.which_screen(img) == name:
                return self.settle(img)
            if click_again and not tried and time.time() > end - (timeout or self.timeout) / 2:
                self.g.click(click_again)
                tried = True
            self.g.sleep(0.15)
        raise FlowError(f"'{name}' 화면이 나오지 않았어요. 게임 화면을 확인해 주세요.")

    def settle(self, img, box=None, tries=12):
        """애니메이션이 끝날 때까지(화면이 멈출 때까지) 기다림"""
        box = box or (0, 0, img.width, img.height)
        prev = img
        for _ in range(tries):
            self.g.sleep(0.12)
            cur = self.g.grab()
            if region_diff(prev, cur, box) < 1.5:
                return cur
            prev = cur
        return prev

    def wait_change(self, before, box, timeout=None, thr=6.0):
        end = time.time() + (timeout or self.timeout)
        while time.time() < end:
            self.g.sleep(0.12)
            img = self.g.grab()
            if region_diff(before, img, box) > thr:
                return self.settle(img)
        return None

    def click_until(self, pt, cond, tries=6, timeout=None):
        """pt를 누르고 cond(화면)이 참이 될 때까지 기다림. 안 되면 클릭 방식을 바꿔 가며 다시 누름"""
        per = (timeout or self.timeout) / 2
        methods = getattr(self.g, 'METHODS', (None,))
        prefer = getattr(self.g, 'prefer', methods[0])
        order = [prefer] + [m for m in methods if m != prefer]
        for k in range(tries):
            m = order[(k // 2) % len(order)] if len(order) > 1 else order[0]
            self.g.click(pt, m) if m else self.g.click(pt)
            end = time.time() + per
            while time.time() < end:
                self.g.sleep(0.12)
                im = self.g.grab()
                if cond(im):
                    if m and getattr(self.g, 'prefer', None) != m:
                        self.g.prefer = m
                        self.log(f'  (클릭 방식: {"창에 직접 보내기" if m == "message" else "마우스 입력"})')
                    return self.settle(im)
        return None

    # ------------------------------------------------------------ 선수 상세 읽기
    def _read(self, img, pitcher, ctx):
        if R.detail_page(img) == 1:
            return 1, R.read_page1(img, ctx)
        return 2, R.read_page2(img, self.sn, pitcher, ctx)

    def read_player(self, img, pitcher, ctx):
        """지금 페이지를 읽고, 다른 페이지로 넘겨서 마저 읽음 (지그재그)"""
        mark = len(R.PENDING_LOG)
        page, a = self._read(img, pitcher, ctx)
        btn = DETAIL['page_right'] if page == 1 else DETAIL['page_left']
        img2 = self.click_until(btn, lambda im: R.which_screen(im) == 'detail' and R.detail_page(im) != page)
        if img2 is None:
            raise FlowError(f'{ctx}: 페이지가 넘어가지 않았어요.')
        page2, b = self._read(img2, pitcher, ctx)
        shots = {page: img, page2: img2}
        p1, p2 = (a, b) if page == 1 else (b, a)
        if p1.get('pitcher') != pitcher:
            self.log(f'  ⚠ {ctx}: 타자/투수 구분이 예상과 달라요 (화면 기준으로 처리)')
        # 1페이지 이름과 2페이지 제목 이름이 다르면 페이지를 넘기다 다른 선수로 넘어간 것 -> 멈추고 다시 읽기
        n1, n2 = p1.get('name') or '', R.clean_name(p2.get('name2') or '')
        if n1 and n2 and n1[0] != n2[0] and difflib.SequenceMatcher(None, n1, n2).ratio() < 0.34:
            # 정말 다른 선수로 넘어갔는지, 이름 글자를 잘못 읽은 것뿐인지 확인:
            # 처음 읽은 페이지로 되돌아가서 그 페이지 이름을 다시 읽어 봄.
            # 처음과 같은 이름이면 화면 속 선수는 그대로인 것 -> 글자 오독(예: 이정훈 2페이지 제목을 '롭수'로 읽음)
            img3, verdict = self._same_player_check(page, page2, a, b, ctx)
            if verdict == 'diff':
                path = self.save_diag(f'이름불일치_{n1}_{n2}')
                raise FlowError(f'{ctx}: 1페이지({n1})와 2페이지({n2}) 선수가 달라요. 페이지를 넘기다 다른 선수로 넘어간 것 같아요. (화면 저장: {path})')
            if verdict == 'reread':
                # 처음 페이지를 다시 보니 두 번째 페이지와 같은 선수 -> 처음 읽은 값은 버리고 지금 화면으로 다시 읽음
                _, a = self._read(img3, pitcher, ctx)
                shots[page] = img3
                p1, p2 = (a, b) if page == 1 else (b, a)
                self.log(f'  (참고) {ctx}: 이름이 "{n1}"/"{n2}"로 달라서 처음 페이지를 다시 읽었어요')
            else:
                self.log(f'  (참고) {ctx}: 2페이지 제목을 "{n2}"(으)로 잘못 읽었지만 다시 보니 같은 선수예요 → 이름은 "{n1}"로 써요')
            p2 = dict(p2, name2=p1.get('name') or n1)
            img2, page2 = img3, page                    # 지금 화면은 처음 페이지로 돌아와 있음
        player = dict(p1)
        player.update({k: v for k, v in p2.items() if k not in ('warn', 'name2')})
        if not player.get('name'):                       # 1페이지에서 이름을 못 읽으면 2페이지 제목 사용
            player['name'] = p2.get('name2') or ''
        player['pitcher'] = p1.get('pitcher', pitcher)
        if p1.get('card_banner'):                      # 위쪽 배너("올스타 카드" 등)가 있으면 그게 가장 확실
            player['card_type'] = p1['card_banner']
        player.pop('card_banner', None)
        player['warn'] = (p1.get('warn') or []) + (p2.get('warn') or [])
        pend = [x for x in R.PENDING_LOG[mark:] if x[1] in ('level', 'angle_digit')]   # 카드 종류는 확인 안 함
        if self.helper and (player['warn'] or pend):
            entry = (player, shots, ctx, pend)
            if self.claude_now:
                self.check_list.append(entry)
                self.claude_verify()           # 이 카드 화면에 머문 채로 바로 확인
            else:
                self.check_list.append(entry)
        return player, img2, page2

    def _same_player_check(self, page, page2, first, second, ctx):
        """처음 읽은 페이지(page)로 되돌아가 이름을 다시 읽음 -> (화면, 판정)
        'same'   : 처음 읽은 이름과 같음 = 화면 속 선수는 그대로, 이름 글자만 잘못 읽은 것
        'reread' : 두 번째 페이지 이름과 같음 = 처음 읽은 값이 의심스러우니 처음 페이지를 다시 읽기
        'diff'   : 둘 다 아님 = 정말 다른 선수로 넘어간 것으로 보고 멈춤"""
        back = DETAIL['page_left'] if page2 == 2 else DETAIL['page_right']
        img3 = self.click_until(back, lambda im: R.which_screen(im) == 'detail' and R.detail_page(im) == page)
        if img3 is None:
            return None, 'diff'

        def nm(d, pg):
            return (d.get('name') or '') if pg == 1 else R.clean_name(d.get('name2') or '')

        def like(x, y):
            return bool(x and y) and difflib.SequenceMatcher(None, x, y).ratio() >= 0.6

        again = R.read_name(img3, P1['name'] if page == 1 else P2['title'])
        if like(again, nm(first, page)):
            return img3, 'same'
        if like(again, nm(second, page2)):
            return img3, 'reread'
        return img3, 'diff'

    def claude_verify(self):
        """연동 중이면 확실하지 않은 선수들을 Claude에게 한 번에 확인받음"""
        lst, self.check_list = self.check_list, []
        if not (self.helper and lst):
            return
        import claude_help as CH
        from claude_link import ClaudeError
        try:
            CH.verify_players(self.helper, lst, self.sn, self.log)
        except ClaudeError as e:
            self.log(f'  ⚠ Claude 확인 실패: {e} (프로그램이 읽은 값 그대로 계산해요)')

    def claude_fill_labels(self, img, rows, boxes, where):
        """내 선발 번호를 두 줄 이상 못 읽었을 때 Claude에게 라벨 그림을 보여 주고 채움"""
        if not self.helper or sorted(r.get('my_sp') or 0 for r in rows) == [1, 2, 3, 4, 5]:
            return
        import claude_help as CH
        from claude_link import ClaudeError
        nums = [r.get('my_sp') for r in rows]
        miss = [i + 1 for i, r in enumerate(rows) if not r.get('my_sp') or nums.count(r.get('my_sp')) > 1]
        self.log(f'  {where} 화면에서 {miss}번째 줄의 내 선발 번호를 못 읽어서 Claude에게 확인할게요...')
        try:
            got = CH.read_rotation_labels(self.helper, [img.crop(b) for b in boxes])
        except ClaudeError as e:
            self.log(f'  ⚠ Claude 확인 실패: {e}')
            return
        if sorted(g or 0 for g in got) == [1, 2, 3, 4, 5]:
            for r, g in zip(rows, got):              # Claude가 다섯 줄을 모두 읽었으면 그 답을 씀
                r['my_sp'] = g
        else:
            for r, g in zip(rows, got):
                if (not r.get('my_sp') or nums.count(r.get('my_sp')) > 1) and g:
                    r['my_sp'] = g
        self.log('  ✓ Claude 확인: ' + ', '.join(f'{i + 1}줄={r.get("my_sp") or "?"}' for i, r in enumerate(rows)))
        CH.write_memo('선발 번호 라벨', where, '', [f'{m}번째 줄' for m in miss],
                      [f'{i + 1}줄={rows[i].get("my_sp") or "?"}' for i in range(len(rows))], '', None)

    def claude_rescue(self, goal, err, is_done):
        if not self.helper:
            return False
        import claude_help as CH
        from claude_link import ClaudeError
        self.log('  → Claude에게 화면 확인을 부탁할게요...')
        try:
            ok, _ = CH.rescue(self.helper, self.g, self.g.grab(), goal, err, is_done, self.log)
            return ok
        except ClaudeError as e:
            self.log(f'  ⚠ Claude 도움 실패: {e}')
            return False

    def traverse(self, n, pitcher_of, ctx):
        """상세 화면에서 n명을 차례로 읽음. pitcher_of(i) -> 그 선수가 투수인지"""
        img = self.wait_screen('detail')
        out = []
        for i in range(n):
            p, img, page = self.read_player(img, pitcher_of(i), f'{ctx} {i + 1}번째')
            nm = p.get('name')
            if self.validate and i > 0 and nm and nm == out[0].get('name') and p.get('pitcher') == out[0].get('pitcher'):
                # 목록 끝에서 처음 선수로 돌아옴 = 중간에 '다음 선수'가 두 번 눌려 한 명을 건너뛴 것
                raise FlowError(f'{ctx}: {i + 1}번째에서 첫 선수({nm})로 돌아왔어요. 중간에 한 명을 건너뛴 것 같아요.')
            warn = f"  (확인 필요: {', '.join(p['warn'])})" if p['warn'] else (' ✓Claude 확인' if p.get('claude_checked') else '')
            self.log(f'  {i + 1}. {nm}  OVR {p.get("ovr")}{warn}')
            out.append(p)
            if i < n - 1:
                box = (280, 120, 640, 650)        # 위쪽 카드 부분: 1·2페이지 모두 선수마다 바뀜
                before = img
                # 다시 누르기 전에 충분히 기다림 (늦게 넘어가는데 또 누르면 두 명이 넘어가서 한 명을 건너뜀)
                nxt = self.click_until(DETAIL['next_player'],
                                       lambda im: R.which_screen(im) == 'detail' and region_diff(before, im, box) > 6.0,
                                       tries=4, timeout=self.timeout * 2)
                if nxt is None:
                    raise FlowError(f'{ctx}: 다음 선수로 넘어가지 않았어요.')
                img = nxt
        return out

    def close_to(self, screen, btn=None):
        if self.click_until(btn or DETAIL['close'], lambda im: R.which_screen(im) == screen) is None:
            raise FlowError(f"닫기를 눌러도 '{screen}' 화면으로 돌아가지 않았어요.")

    def open_detail_from_lineup(self, card_pt, what):
        """라인업 카드 한 번 누르기 -> 아래 팝업의 [상세 정보] 누르기 -> 상세 화면"""
        if self.click_until(card_pt, R.card_popup_open) is None:
            for t in getattr(self.g, 'traces', []):
                self.log('  [진단] ' + t)
            raise FlowError(f'{what} 카드를 눌러도 아래쪽 [상세 정보] 버튼이 안 떴어요. '
                            '클릭이 게임에 전달되지 않는 것 같아요. 실행.bat을 오른쪽 클릭 → [관리자 권한으로 실행]으로 켜 보세요.')
        if self.click_until(LINEUP['detail_btn'], lambda im: R.which_screen(im) == 'detail') is None:
            raise FlowError(f'{what}: [상세 정보]를 눌러도 상세 화면이 안 열렸어요.')

    # ------------------------------------------------------------ 1) 내 데이터 갱신
    def read_my_deck(self):
        self.g.activate()
        img = self.g.grab()
        if R.which_screen(img) != 'lineup':
            raise FlowError('게임에서 [라인업] 화면을 열어 둔 상태로 눌러 주세요.')
        if not R.is_ranking_mode(img):
            raise FlowError("라인업 왼쪽 아래가 '랭킹 챌린지'가 아니에요. 랭킹 챌린지 라인업으로 바꿔 주세요.")
        self.check_list = []
        self.log('내 타자 9명 읽는 중...')
        if not R.tab_selected(img, 'batter'):
            if self.click_until(LINEUP['tab_batter'], lambda im: R.tab_selected(im, 'batter')) is None:
                raise FlowError('타자 탭이 눌리지 않았어요.')
        self.open_detail_from_lineup(LINEUP['card_dh'], '지명타자')
        bats = self.traverse(9, lambda i: False, '내 타자')
        self.close_to('lineup')
        self.log('내 선발투수 5명 읽는 중...')
        if self.click_until(LINEUP['tab_pitcher'], lambda im: R.tab_selected(im, 'pitcher')) is None:
            raise FlowError('투수 탭이 눌리지 않았어요.')
        self.open_detail_from_lineup(LINEUP['card_sp1'], '1선발')
        pits = self.traverse(5, lambda i: True, '내 선발')
        self.close_to('lineup')
        self.check_lineup(bats, None, '내 타자')
        pn = [p.get('name') for p in pits if p.get('name')]
        if self.validate and (len(set(pn)) < len(pn) or any(not p.get('pitcher') for p in pits)):
            raise FlowError('내 선발 5명 중 같은 선수/타자가 섞였어요. 다시 [① 내 덱 읽기]를 눌러 주세요.')
        self.claude_verify()

        old = ST.load_json(ST.MY_DECK) or {}
        old_slots = {(R.clean_name(b.get('name')), b.get('pos')): b.get('slot') for b in old.get('batters', [])}
        for i, b in enumerate(bats):
            if b.get('pos') not in BAT_POS_ORDER:
                b['pos'] = BAT_POS_ORDER[i]
            b['slot'] = old_slots.get((b.get('name'), b.get('pos')))
        for i, p in enumerate(pits):
            p['rotation'] = i + 1
        deck = {'time': ST.now(), 'batters': bats, 'pitchers': pits}
        self._avg_scores(deck)
        return deck

    @staticmethod
    def _avg_scores(deck):
        for b in deck['batters']:
            b['avg_score'] = SC.batter_score(b, {})['total']
        for p in deck['pitchers']:
            p['avg_score'] = SC.pitcher_score(p, {})['total']

    @staticmethod
    def save_deck(deck):
        Runner._avg_scores(deck)
        ST.save_json(ST.MY_DECK, deck)
        return ST.rebuild_excel()

    # ------------------------------------------------------------ 2) 상대 분석
    def analyze(self):
        deck = ST.load_json(ST.MY_DECK)
        if not deck:
            raise FlowError('먼저 [① 내 덱 읽기]를 해 주세요.')
        slots = sorted(b.get('slot') or 0 for b in deck['batters'])
        if slots != list(range(1, 10)):
            raise FlowError('내 타순이 비어 있거나 겹쳐요. [타순 입력]에서 1~9번을 정해 주세요.')
        self.g.activate()
        img = self.g.grab()
        if R.which_screen(img) != 'lobby':
            raise FlowError('게임에서 랭킹 챌린지 [로비] 화면을 열어 둔 상태로 눌러 주세요.')
        lobby = R.read_lobby(img)
        self.check_list = []
        if any(r['result'] for r in lobby):
            return {'has_results': True}

        self.log('상대 정보 확인 중...')
        img = self.click_until(LOBBY['order_button'], lambda im: R.which_screen(im) == 'order')
        if img is None:
            raise FlowError('[순서 변경] 화면이 열리지 않았어요.')
        labels = R.read_order_labels(img)
        self.claude_fill_labels(img, labels, [order_row(i)['my_label'] for i in range(5)], '순서 변경')
        # 내 선발 손(좌/우)을 못 읽었으면 순서 변경 화면 라벨("1선발 L")로 채움
        for lab in labels:
            n, h = lab.get('my_sp'), lab.get('my_hand')
            if n and h in ('L', 'R') and 1 <= n <= len(deck['pitchers']) and deck['pitchers'][n - 1].get('throw') not in ('L', 'R'):
                deck['pitchers'][n - 1]['throw'] = h
        opps = []
        failed = []
        for i in range(5):
            self.log(f'[{i + 1}번째 상대] {lobby[i]["nick"]}')
            got = None
            for attempt in range(2):                      # 실패하면 한 번 더 시도
                n_check = len(self.check_list)
                try:
                    got = self.read_opponent(i, lobby, labels, reopen=(i > 0 or attempt > 0))
                    break
                except FlowError as e:
                    del self.check_list[n_check:]
                    path = self.save_diag(f'상대{i + 1}_실패{attempt + 1}')
                    self.log(f'  ⚠ {e}  (화면 저장: {path})')
                    self.claude_rescue('랭킹 챌린지 로비 화면(상대 5명 목록과 PLAY BALL 버튼이 보이는 화면)으로 돌아가기',
                                       str(e), lambda im: R.which_screen(im) == 'lobby')
                    if not self.recover_to_lobby():
                        raise FlowError('로비 화면으로 돌아가지 못해서 멈췄어요. 게임 화면을 로비로 돌려놓고 다시 눌러 주세요.')
                    if attempt == 0:
                        self.log('  → 이 상대를 처음부터 다시 읽을게요.')
            if got is None:
                self.log(f'  ✗ {i + 1}번째 상대는 건너뛰어요. (추천에서 빠짐)')
                failed.append(i + 1)
                continue
            opps.append(got)
        if not opps:
            raise FlowError('상대를 한 명도 읽지 못했어요.')
        if failed:
            self.log(f'⚠ 못 읽은 상대: {failed}번째 줄 — 나머지 상대만으로 추천해요.')
        self.claude_verify()

        return self.compute_and_save(deck, opps)

    def read_opponent(self, i, lobby, labels, reopen):
        if reopen and self.click_until(LOBBY['order_button'], lambda im: R.which_screen(im) == 'order') is None:
            raise FlowError('[순서 변경] 화면이 열리지 않았어요.')
        row = order_row(i)
        self.g.click(row['click'])
        self.g.sleep(self.wait_click + 0.3)
        if self.click_until(row['opp_sp'], lambda im: R.which_screen(im) == 'detail') is None:
            raise FlowError(f'{i + 1}번째 상대 선발 카드를 눌러도 상세 화면이 안 열렸어요.')
        players = self.traverse(10, lambda k: k == 0, f'상대{i + 1}')
        sp, bats = players[0], players[1:]
        self.check_lineup(bats, sp, f'상대{i + 1}')
        sp['rotation'] = labels[i].get('opp_sp')
        if labels[i].get('opp_hand') in ('L', 'R') and sp.get('throw') not in ('L', 'R'):
            sp['throw'] = labels[i]['opp_hand']
        for k, b in enumerate(bats):
            b['slot'] = k + 1
        self.close_to('order')
        self.close_to('lobby', ORDER['close'])
        self.g.sleep(0.3)
        return {'row': i + 1, 'lobby': lobby[i], 'label': labels[i], 'sp': sp, 'batters': bats}

    def check_lineup(self, bats, sp, ctx):
        """타자 9명이 서로 다르고 투수가 섞이지 않았는지 확인 (게임에서 투수는 절대 타석에 서지 않음)"""
        if not self.validate:
            return
        names = [b.get('name') for b in bats if b.get('name')]
        bad = [b.get('name') for b in bats if b.get('pitcher')]
        if sp is not None and sp.get('name') in names:
            bad.append(sp.get('name'))
        dup = {n for n in names if names.count(n) > 1}
        if bad or dup:
            raise FlowError(f'{ctx}: 타자 목록에 투수/같은 선수가 섞였어요 ({", ".join(sorted(set(bad) | dup))}). '
                            '한 명을 건너뛰고 읽은 것 같아요.')

    def save_diag(self, tag):
        """문제가 생긴 순간의 화면을 '진단' 폴더에 저장"""
        try:
            d = os.path.join(ST.ROOT_DIR, '진단')
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, ST.now().replace(':', '').replace(' ', '_') + f'_{tag}.png')
            self.g.grab().save(path)
            return path
        except Exception as e:
            return f'저장 실패 ({e})'

    def recover_to_lobby(self):
        """상세 화면/순서 변경 화면에서 닫기를 눌러 로비로 돌아감"""
        for _ in range(6):
            im = self.g.grab()
            scr = R.which_screen(im)
            if scr == 'lobby':
                self.g.sleep(0.3)
                return True
            if scr == 'detail':
                self.g.click(DETAIL['close'])
            elif scr == 'order':
                self.g.click(ORDER['close'])
            else:
                self.g.click(DETAIL['close'])              # 모르는 화면: 아래 X 위치를 눌러 봄
            self.g.sleep(1.2)
        return R.which_screen(self.g.grab()) == 'lobby'

    @staticmethod
    def compute_and_save(deck, opps):
        my_bat, my_sps = deck['batters'], deck['pitchers']
        import winmodel as W
        wm = W.model()
        M, best = SC.best_assignment(my_bat, my_sps, [(o['batters'], o['sp']) for o in opps],
                                     value=lambda d: W.exp_points(d, wm))      # 기대 승점이 가장 큰 배치
        best_sum = SC.best_assignment(my_bat, my_sps, [(o['batters'], o['sp']) for o in opps])[1]   # 참고용: 점수차 합 기준
        for row in M:
            for c in row:
                c['p_win'] = W.p_win(c['diff'], wm)
                c['exp_pts'] = W.exp_points(c['diff'], wm)
        t = ST.now()
        rec = []
        for j, o in enumerate(opps):
            cur = o['label'].get('my_sp')
            rec.append({'row': o['row'], 'col': j, 'recommend': best[1][j] + 1, 'current': cur,
                        'recommend_sum': best_sum[1][j] + 1,
                        'p_win': [round(M[i][j]['p_win'], 3) for i in range(len(my_sps))],
                        'diffs': [round(M[i][j]['diff'], 2) for i in range(len(my_sps))],
                        'my_sp': [round(M[i][j]['my_sp'], 2) for i in range(len(my_sps))],
                        'opp_lineup': [round(M[i][j]['opp_lineup'], 2) for i in range(len(my_sps))],
                        'my_lineup': round(M[0][j]['my_lineup'], 2), 'opp_sp': round(M[0][j]['opp_sp'], 2)})
        pending = {'time': t, 'deck_time': deck.get('time'), 'deck': copy.deepcopy(deck),
                   'opps': opps, 'recommend': rec, 'recorded': [False] * len(opps)}
        ST.save_json(ST.PENDING, pending)
        ST.append_jsonl(ST.ANALYSES, [{
            '분석시각': t, '줄': r['row'], '상대닉네임': opps[r['col']]['lobby'].get('nick'),
            '상대선발': opps[r['col']]['sp'].get('name'), '상대선발손': opps[r['col']]['sp'].get('throw'),
            '추천 내선발': r['recommend'], '점수차합 기준 추천': r['recommend_sum'],
            **{f'내{i + 1}선발 점수차': r['diffs'][i] for i in range(len(r['diffs']))},
            **{f'내{i + 1}선발 예상승률': r['p_win'][i] for i in range(len(r['p_win']))},
            **{f'내{i + 1}선발 투수점수': r['my_sp'][i] for i in range(len(r['my_sp']))},
            **{f'상대타선(vs 내{i + 1}선발)': r['opp_lineup'][i] for i in range(len(r['opp_lineup']))},
            '내 타선점수': r['my_lineup'], '상대 선발점수': r['opp_sp']} for r in rec])
        err = ST.rebuild_excel()
        return {'has_results': False, 'pending': pending, 'matrix': M, 'best': best, 'best_sum': best_sum,
                'model': wm, 'excel_error': err}

    # ------------------------------------------------------------ 3) 결과 기록
    def record_results(self, img=None):
        pending = ST.load_json(ST.PENDING)
        if not pending:
            return {'recorded': 0, 'msg': '기록할 분석이 없어요. (상대 분석을 먼저 해야 결과를 짝지을 수 있어요)'}
        if img is None:
            self.g.activate()
            img = self.g.grab()
        if R.which_screen(img) != 'lobby':
            raise FlowError('랭킹 챌린지 [로비] 화면에서 눌러 주세요.')
        lobby = R.read_lobby(img)
        self.claude_fill_labels(img, lobby, [lobby_row(i)['my_label'] for i in range(5)], '로비')
        return self.record_from_lobby(pending, lobby)

    @staticmethod
    def record_from_lobby(pending, lobby):
        deck = pending['deck']
        my_bat, my_sps = deck['batters'], deck['pitchers']
        t = ST.now()
        n = 0
        mrows, prows, skipped = [], [], []
        for j, o in enumerate(pending['opps']):
            if pending['recorded'][j]:
                continue
            ri = o.get('row', j + 1) - 1
            row = lobby[ri]
            if not row.get('result'):
                continue
            old = o['lobby']
            same = (row.get('point') == old.get('point')) or (row.get('ovr') == old.get('ovr'))
            if not same:
                skipped.append(ri + 1)
                continue
            lab = row.get('my_sp') or o['label'].get('my_sp')
            if not lab or not (1 <= lab <= len(my_sps)):
                skipped.append(ri + 1)
                continue
            my_sp = my_sps[lab - 1]
            m = SC.matchup(my_bat, my_sp, o['batters'], o['sp'])
            rec = pending['recommend'][j]['recommend']
            sc = (row.get('score') or ':').split(':')
            mrows.append({
                '기록시각': t, '분석시각': pending['time'], '줄': ri + 1, '상대닉네임': old.get('nick'),
                '상대POINT': old.get('point'), '상대OVR(로비)': old.get('ovr'), '상대타자OVR': o['label'].get('opp_bat_ovr'),
                '상대선발': o['sp'].get('name'), '상대선발손': o['sp'].get('throw'), '상대선발순번': o['sp'].get('rotation'),
                '내선발': my_sp.get('name'), '내선발손': my_sp.get('throw'), '내선발순번': lab,
                '추천선발순번': rec, '추천따름': 'Y' if rec == lab else 'N',
                '내타선점수': round(m['my_lineup'], 2), '내선발점수': round(m['my_sp'], 2),
                '상대타선점수': round(m['opp_lineup'], 2), '상대선발점수': round(m['opp_sp'], 2),
                '내합계': round(m['my_total'], 2), '상대합계': round(m['opp_total'], 2), '점수차': round(m['diff'], 2),
                '예상승률': round(_pwin(m['diff']), 3),
                '내팀OVR': round(m['my_team_ovr'], 2) if m['my_team_ovr'] else None,
                '상대팀OVR': round(m['opp_team_ovr'], 2) if m['opp_team_ovr'] else None,
                '결과': _result_from_score(row.get('score')) or row.get('result'), '스코어': row.get('score'),
                '내득점': int(sc[0]) if sc[0].isdigit() else None, '상대득점': int(sc[1]) if sc[1].isdigit() else None})
            base = {'기록시각': t, '분석시각': pending['time'], '줄': ri + 1}
            prows += _player_rows(base, '나', my_bat, m['my_lineup_detail'], my_sp, m['my_sp_detail'], lab)
            prows += _player_rows(base, '상대', o['batters'], m['opp_lineup_detail'], o['sp'], m['opp_sp_detail'],
                                  o['sp'].get('rotation'))
            pending['recorded'][j] = True
            n += 1
        if n:
            ST.append_jsonl(ST.MATCHES, mrows)
            ST.append_jsonl(ST.PLAYERS, prows)
            ST.save_json(ST.PENDING, pending)
        err = ST.rebuild_excel() if n else None
        msg = f'{n}경기 결과를 기록했어요.'
        if skipped:
            msg += f' (줄 {skipped}은 직전 분석의 상대와 달라 보여서 건너뛰었어요)'
        return {'recorded': n, 'msg': msg, 'excel_error': err,
                'left': sum(1 for x in pending['recorded'] if not x)}


def _pwin(diff):
    import winmodel as W
    return W.p_win(diff)


def _result_from_score(score):
    """'4:6' (내 점수:상대 점수) -> 승/패/무. 스코어를 못 읽었으면 None"""
    try:
        a, b = (int(x) for x in str(score).split(':'))
    except (TypeError, ValueError):
        return None
    return '승' if a > b else ('패' if a < b else '무')


def _player_rows(base, team, bats, bat_det, sp, sp_det, rot):
    rows = []
    for p, d, role, order in [(sp, sp_det, '선발', rot)] + [(b, bd, '타자', b.get('slot')) for b, bd in zip(bats, bat_det)]:
        st, g, sk = ST.player_row_values(p)
        sks = (d.get('skills') or []) + [('', None, None)] * 3
        extra = d.get('angle', 0) + d.get('pen', 0) if role == '타자' else d.get('lefty', 0)
        rows.append({**base, '팀': team, '역할': role, '타순/순번': order, '이름': p.get('name'), '포지션': p.get('pos'),
                     'OVR': p.get('ovr'), '카드OVR': p.get('ovr_seen'), '스탯평균OVR': p.get('ovr_calc'),
                     '별': p.get('stars'), '카드종류': p.get('card_type'),
                     '투': p.get('throw'), '타': p.get('bat'),
                     '파워/구속': st[0], '정확/변화': st[1], '선구/구위': st[2], '인내/제구': st[3],
                     '주루/지구력': st[4], '수비': st[5], '발사각': st[6],
                     '잠재1': g[0], '잠재2': g[1], '잠재3': g[2], '잠재4': g[3], '잠재5': g[4], '각잠': g[5],
                     '스킬1': sks[0][0], 'Lv1': sks[0][1], '점수1': sks[0][2],
                     '스킬2': sks[1][0], 'Lv2': sks[1][1], '점수2': sks[1][2],
                     '스킬3': sks[2][0], 'Lv3': sks[2][1], '점수3': sks[2][2],
                     '능력치점수': round(d['stat'], 2), '잠재점수': d['pot'], '기타보정': round(extra, 2),
                     '스킬합': round(d['skill'], 2), '매치업보정점수': round(d['total'], 2)})
    return rows

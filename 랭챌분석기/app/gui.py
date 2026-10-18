# -*- coding: utf-8 -*-
"""랭챌 분석기 버튼 화면

화면 구성 (2026-10-18 정리)
  위 메뉴   : 파일 / 학습 / 도구 / 설정  (가끔 쓰는 기능)
  왼쪽      : 자주 쓰는 순서대로 버튼 (① 내 덱 → ② 상대 분석 → ③ 결과 기록, ④ 자동 진행, 중지)
  오른쪽 탭 : [추천 배치] 표 / [상세표] 점수 표 / [내 덱] / [진행 기록]
  아래 줄   : 지금 상태 · 에뮬레이터 · Claude 연동 · 학습 대기
"""
import os, sys, json, math, queue, threading, traceback, subprocess
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from PIL import Image, ImageTk

import recognize as R
import score as SC
import storage as ST
from flows import Runner, FlowError
from game import StopRequested

if sys.platform == 'win32':
    UI_FAMILY, MONO_FAMILY = '맑은 고딕', 'D2Coding'
else:
    UI_FAMILY, MONO_FAMILY = 'Noto Sans CJK KR', 'Noto Sans Mono CJK KR'
FONT = (UI_FAMILY, 10)
MONO = (MONO_FAMILY, 10)

# 색 (밝은 테마)
C = {'bg': '#F4F5F7', 'card': '#FFFFFF', 'line': '#E2E5EA', 'text': '#1F2328', 'muted': '#6B7280',
     'accent': '#2563EB', 'accent_dark': '#1D4ED8', 'accent_soft': '#EEF3FF',
     'good': '#0F7B3F', 'good_bg': '#EAF7EF', 'bad': '#C0262D', 'bad_bg': '#FDEDEE',
     'warn_bg': '#FFF7E0', 'stop': '#C0262D'}


def setup_style(root):
    """ttk 기본 모양(clam)에 색과 글꼴을 입힘. 다른 창(학습 대기, Claude 창 등)도 같은 모양이 됨"""
    st = ttk.Style(root)
    st.theme_use('clam')
    fams = set(tkfont.families(root))
    if MONO_FAMILY not in fams:
        # D2Coding이 없으면: 한글 윈도우에 기본으로 있는 고정폭 글꼴 '굴림체' (한글 = 영문 두 칸이라 표 줄이 맞음)
        for f in ('D2Coding', '굴림체', 'GulimChe', '돋움체', 'DotumChe', 'Consolas', 'Courier'):
            if f in fams:
                globals()['MONO'] = (f, 10)
                break
    root.option_add('*Toplevel.background', C['bg'])          # 다른 창(자동 진행, 학습 대기 등) 바탕색도 맞춤
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
        try:
            tkfont.nametofont(name).configure(family=UI_FAMILY, size=10)
        except tk.TclError:
            pass
    root.configure(bg=C['bg'])
    st.configure('.', background=C['bg'], foreground=C['text'], font=FONT, bordercolor=C['line'],
                 lightcolor=C['line'], darkcolor=C['line'], troughcolor=C['bg'], focuscolor=C['accent'])
    st.configure('TFrame', background=C['bg'])
    st.configure('Card.TFrame', background=C['card'])
    st.configure('TLabel', background=C['bg'])
    st.configure('Card.TLabel', background=C['card'])
    st.configure('Title.TLabel', background=C['card'], font=(UI_FAMILY, 14, 'bold'))
    st.configure('Section.TLabel', background=C['card'], foreground=C['muted'], font=(UI_FAMILY, 9, 'bold'))
    st.configure('Hint.TLabel', background=C['card'], foreground=C['muted'], font=(UI_FAMILY, 9))
    st.configure('Muted.TLabel', foreground=C['muted'])
    st.configure('Big.TLabel', background=C['card'], font=(UI_FAMILY, 12, 'bold'))
    st.configure('Status.TLabel', background=C['card'], foreground=C['muted'], font=(UI_FAMILY, 9))
    st.configure('StatusBold.TLabel', background=C['card'], foreground=C['text'], font=(UI_FAMILY, 9, 'bold'))
    # 버튼
    st.configure('TButton', background=C['card'], padding=(12, 6), relief='flat', borderwidth=1)
    st.map('TButton', background=[('disabled', C['bg']), ('pressed', C['line']), ('active', C['accent_soft'])],
           foreground=[('disabled', '#A0A6AE')])
    st.configure('Side.TButton', background=C['card'], anchor='w', padding=(14, 9), font=(UI_FAMILY, 11),
                 borderwidth=0)
    st.map('Side.TButton', background=[('disabled', C['card']), ('pressed', C['line']), ('active', C['accent_soft'])],
           foreground=[('disabled', '#A0A6AE')])
    st.configure('Accent.TButton', background=C['accent'], foreground='#FFFFFF', anchor='w', padding=(14, 10),
                 font=(UI_FAMILY, 11, 'bold'), borderwidth=0)
    st.map('Accent.TButton', background=[('disabled', '#A9C1F5'), ('pressed', C['accent_dark']), ('active', C['accent_dark'])],
           foreground=[('disabled', '#FFFFFF')])
    st.configure('Primary.TButton', background=C['accent'], foreground='#FFFFFF', padding=(18, 7),
                 font=(UI_FAMILY, 10, 'bold'), borderwidth=0)
    st.map('Primary.TButton', background=[('pressed', C['accent_dark']), ('active', C['accent_dark'])])
    st.configure('Stop.TButton', background=C['card'], foreground=C['stop'], anchor='w', padding=(14, 8),
                 font=(UI_FAMILY, 10, 'bold'), borderwidth=0)
    st.map('Stop.TButton', background=[('pressed', C['line']), ('active', C['bad_bg'])])
    # 탭
    st.configure('TNotebook', background=C['bg'], borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure('TNotebook.Tab', background=C['bg'], foreground=C['muted'], padding=(16, 7), borderwidth=0,
                 font=(UI_FAMILY, 10))
    st.map('TNotebook.Tab', background=[('selected', C['card'])], foreground=[('selected', C['text'])],
           font=[('selected', (UI_FAMILY, 10, 'bold'))])
    # 표
    line = tkfont.Font(root, font=FONT).metrics('linespace')
    st.configure('Treeview', background=C['card'], fieldbackground=C['card'], rowheight=line + 14, borderwidth=0)
    st.configure('Treeview.Heading', background=C['bg'], foreground=C['muted'], font=(UI_FAMILY, 9, 'bold'),
                 relief='flat', padding=(6, 6))
    st.map('Treeview', background=[('selected', C['accent_soft'])], foreground=[('selected', C['text'])])
    st.map('Treeview.Heading', background=[('active', C['line'])])
    st.configure('TLabelframe', background=C['bg'])
    st.configure('TLabelframe.Label', background=C['bg'], foreground=C['muted'])
    st.configure('TCheckbutton', background=C['bg'])
    st.configure('TRadiobutton', background=C['bg'])
    st.configure('Horizontal.TProgressbar', background=C['accent'], troughcolor=C['line'], borderwidth=0, thickness=4)
    return st


class App:
    def __init__(self, root):
        self.root = root
        root.title('랭챌 분석기')
        root.geometry('1120x720')
        root.minsize(900, 560)
        self.q = queue.Queue()
        self.busy = False
        self.game = None
        self.helper = None
        self.status = tk.StringVar(value='준비됨')
        self.claude_on = tk.BooleanVar(value=False)
        self.ontop = tk.BooleanVar(value=False)
        self.conn_txt = tk.StringVar(value='에뮬레이터: 아직 연결 안 함')
        self.claude_txt = tk.StringVar(value='Claude 연동: 꺼짐')
        self.learn_txt = tk.StringVar(value='학습 대기 0건')
        try:
            from version import VERSION
        except Exception:
            VERSION = '?'
        self.version = VERSION
        import share_data
        self.share_on = tk.BooleanVar(value=share_data.state().get('consent') is True)

        self._build_menu()
        self._build_statusbar()
        body = ttk.Frame(root, padding=(12, 12, 12, 0))
        body.pack(fill='both', expand=True)
        self._build_sidebar(body)
        self._build_tabs(body)
        root.bind_all('<F12>', lambda e: self.stop())

        tess = R.Tess.find(SC.SET.get('tesseract_경로') or None)
        if not tess:
            self.log('⚠ Tesseract(글자 인식 프로그램)를 찾지 못했어요. 설명서의 설치 방법을 확인해 주세요.', 'bad')
        deck = ST.load_json(ST.MY_DECK)
        if deck:
            self._render_deck(deck)
        self.refresh_learn_count()
        self.root.after(100, self.poll)

    # ------------------------------------------------------------ 화면 만들기
    def _build_menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label='기록 엑셀 열기', command=self.open_excel)
        f.add_command(label='엑셀 수정 반영…', command=self.do_import_excel)
        f.add_separator()
        f.add_command(label='프로그램 폴더 열기', command=lambda: self._open(ST.ROOT_DIR))
        f.add_command(label='설명서 열기', command=lambda: self._open(os.path.join(ST.ROOT_DIR, '설명서.txt')))
        f.add_separator()
        f.add_command(label='끝내기', command=self.root.destroy)
        m.add_cascade(label='파일', menu=f)
        lm = tk.Menu(m, tearoff=0)
        lm.add_command(label='학습 대기 확인', command=self.do_learn)
        lm.add_separator()
        lm.add_command(label='Claude로 일괄 학습', command=self.do_claude_learn)
        lm.add_command(label='학습 내용 점검 (Claude)', command=self.do_claude_audit)
        lm.add_command(label='Claude 진단 메모 열기', command=self.open_memo)
        m.add_cascade(label='학습', menu=lm)
        self.learn_menu = lm
        t = tk.Menu(m, tearoff=0)
        t.add_command(label='진단용 캡처 (지금 게임 화면 저장)', command=self.do_diag)
        t.add_command(label='진단 폴더 열기', command=lambda: self._open_dir(os.path.join(ST.ROOT_DIR, '진단')))
        t.add_separator()
        t.add_command(label='업데이트 확인', command=self.check_update)
        m.add_cascade(label='도구', menu=t)
        s = tk.Menu(m, tearoff=0)
        s.add_checkbutton(label='클로드 코드 연동', variable=self.claude_on, command=self.toggle_claude)
        s.add_checkbutton(label='매주 경기 기록 보내기 (닉네임 제외)', variable=self.share_on, command=self.toggle_share)
        s.add_checkbutton(label='창을 항상 위에', variable=self.ontop,
                          command=lambda: self.root.attributes('-topmost', self.ontop.get()))
        m.add_cascade(label='설정', menu=s)
        self.root.configure(menu=m)

    def _build_sidebar(self, parent):
        side = ttk.Frame(parent, style='Card.TFrame', padding=(10, 14, 10, 12))
        side.pack(side='left', fill='y', padx=(0, 12), pady=(0, 12))
        ttk.Label(side, text='랭챌 분석기', style='Title.TLabel').pack(anchor='w', padx=6)
        ttk.Label(side, text='LDPlayer 900×1600 세로', style='Hint.TLabel').pack(anchor='w', padx=6, pady=(0, 10))
        self.btns = []

        def section(t):
            ttk.Label(side, text=t, style='Section.TLabel').pack(anchor='w', padx=6, pady=(12, 2))

        def btn(text, fn, hint=None, style='Side.TButton'):
            b = ttk.Button(side, text=text, command=fn, style=style, width=18)
            b.pack(fill='x', pady=1)
            if hint:
                ttk.Label(side, text=hint, style='Hint.TLabel').pack(anchor='w', padx=(34, 0), pady=(0, 4))
            self.btns.append(b)
            return b

        section('처음 한 번 · 덱이 바뀌면')
        btn('①  내 덱 읽기', self.do_deck, '라인업 화면에서')
        btn('     타순 입력', self.do_order)
        section('갱신할 때마다')
        btn('②  상대 분석', self.do_analyze, '랭킹 챌린지 로비에서')
        btn('     추천대로 배치', self.do_arrange)
        btn('③  결과 기록', self.do_record, '경기가 끝난 뒤 로비에서')
        ttk.Frame(side, style='Card.TFrame').pack(fill='both', expand=True)
        ttk.Separator(side).pack(fill='x', pady=8)
        btn('④  자동 진행', self.do_auto, style='Accent.TButton')
        stop = ttk.Button(side, text='■  중지   F12', command=self.stop, style='Stop.TButton')
        stop.pack(fill='x', pady=(6, 0))

    def _build_tabs(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(side='left', fill='both', expand=True, pady=(0, 12))
        self.nb = nb
        # [추천 배치]
        t1 = ttk.Frame(nb, style='Card.TFrame', padding=16)
        nb.add(t1, text='추천 배치')
        self.rec_title = ttk.Label(t1, text='아직 분석 결과가 없어요', style='Big.TLabel')
        self.rec_title.pack(anchor='w')
        self.rec_sub = ttk.Label(t1, style='Hint.TLabel', justify='left',
                                 text='① 라인업 화면에서 [내 덱 읽기] → 타순 입력\n'
                                      '② 랭킹 챌린지 로비에서 [상대 분석] → [추천대로 배치] → 경기 → ③ [결과 기록]\n'
                                      '④ [자동 진행]은 이 과정을 갱신까지 반복해요. 작업 중에는 에뮬레이터 화면을 건드리지 마세요.')
        self.rec_sub.pack(anchor='w', pady=(4, 12))
        cols = ('row', 'opp', 'opp_sp', 'rec', 'diff', 'pwin', 'cur', 'chg')
        heads = ('줄', '상대', '상대 선발', '추천 선발', '점수차', '예상 승률', '지금 배치', '')
        widths = (36, 140, 120, 130, 64, 76, 130, 52)
        tv = ttk.Treeview(t1, columns=cols, show='headings', height=5, selectmode='none')
        for c, h, w in zip(cols, heads, widths):
            tv.heading(c, text=h, anchor='w' if c in ('opp', 'opp_sp', 'rec', 'cur') else 'center')
            tv.column(c, width=w, anchor='w' if c in ('opp', 'opp_sp', 'rec', 'cur') else 'center',
                      stretch=c in ('opp', 'rec', 'cur'))
        tv.tag_configure('good', background=C['good_bg'])
        tv.tag_configure('bad', background=C['bad_bg'])
        tv.tag_configure('chg', font=(UI_FAMILY, 10, 'bold'))
        tv.pack(fill='x')
        self.rec_tv = tv
        self.rec_sum = ttk.Label(t1, style='Card.TLabel', font=(UI_FAMILY, 11, 'bold'))
        self.rec_sum.pack(anchor='w', pady=(14, 2))
        self.rec_note = ttk.Label(t1, style='Hint.TLabel', justify='left', wraplength=760)
        self.rec_note.pack(anchor='w')
        self.rec_warn = tk.Label(t1, bg=C['card'], fg=C['bad'], font=(UI_FAMILY, 10), justify='left', anchor='w',
                                 wraplength=760)
        self.rec_warn.pack(anchor='w', fill='x', pady=(8, 0))
        # [상세표] [내 덱] [진행 기록]
        self.detail = self._text_tab(nb, '상세표')
        self.deck_txt = self._text_tab(nb, '내 덱')
        self.text = self._text_tab(nb, '진행 기록')
        self._log_tab = 3

    def _text_tab(self, nb, title):
        f = ttk.Frame(nb, style='Card.TFrame', padding=(14, 12, 4, 12))
        nb.add(f, text=title)
        t = tk.Text(f, font=MONO, wrap='none', relief='flat', borderwidth=0, bg=C['card'], fg=C['text'],
                    highlightthickness=0, padx=4, pady=2, spacing1=1, spacing3=1, insertbackground=C['text'])
        ys = ttk.Scrollbar(f, orient='vertical', command=t.yview)
        xs = ttk.Scrollbar(f, orient='horizontal', command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side='right', fill='y')
        xs.pack(side='bottom', fill='x')
        t.pack(fill='both', expand=True)
        t.tag_configure('good', foreground=C['good'])
        t.tag_configure('bad', foreground=C['bad'])
        t.tag_configure('head', font=(MONO[0], MONO[1], 'bold'))
        t.tag_configure('title', font=(UI_FAMILY, 11, 'bold'), spacing1=8, spacing3=4)
        t.tag_configure('muted', foreground=C['muted'])
        return t

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, style='Card.TFrame', padding=(14, 6))
        bar.pack(side='bottom', fill='x')
        ttk.Separator(self.root).pack(side='bottom', fill='x')
        self.prog = ttk.Progressbar(bar, mode='indeterminate', length=80, style='Horizontal.TProgressbar')
        ttk.Label(bar, textvariable=self.status, style='StatusBold.TLabel').pack(side='left')
        self.prog.pack(side='left', padx=10)
        self.prog.pack_forget()
        ttk.Label(bar, text=f'v{self.version}', style='Status.TLabel').pack(side='right', padx=(14, 0))
        for v in (self.learn_txt, self.claude_txt, self.conn_txt):
            ttk.Label(bar, textvariable=v, style='Status.TLabel').pack(side='right', padx=(14, 0))

    # ------------------------------------------------------------ 공통
    def log(self, msg, tag=None):
        self.q.put(('log', msg, tag))

    def poll(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == 'log':
                    self.text.insert('end', rest[0] + '\n', rest[1] or ())
                    self.text.see('end')
                elif kind == 'call':
                    rest[0]()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def ui(self, fn):
        self.q.put(('call', fn))

    def set_busy(self, b, msg=''):
        self.busy = b
        self.status.set(msg or ('작업 중...' if b else '준비됨'))
        for x in self.btns:
            x.state(['disabled'] if b else ['!disabled'])
        if b:
            self.prog.pack(side='left', padx=10)
            self.prog.start(12)
            self.nb.select(self._log_tab)               # 작업 중에는 진행 기록을 보여 줌
        else:
            self.prog.stop()
            self.prog.pack_forget()

    # ------------------------------------------------------------ 업데이트 / 기록 보내기
    def after_start(self):
        """창이 뜨고 3초 뒤: 업데이트 정상 실행 표시, 기록 보내기 동의(처음 한 번), 기록 보내기 시작"""
        try:
            import updater
            m = updater.confirm_ok()
            if m:
                self.log(f"v{m.get('from')} → v{m.get('to')} 로 업데이트했어요.", 'good')
                for line in (m.get('notes') or '').strip().splitlines()[:12]:
                    self.log('  ' + line)
        except Exception as e:
            self.log(f'(업데이트 확인 표시 실패: {e})')
        import share_data
        if share_data.state().get('consent') is None:
            ok = messagebox.askyesno(
                '경기 기록 보내기',
                '프로그램을 더 정확하게 만들기 위해, 매주 지난주 경기 기록을 개발자에게 보내도 될까요?\n\n'
                '· 보내는 것: 매치 기록(점수차·승패·선발)과 양쪽 선수 카드 정보\n'
                '· 보내지 않는 것: 상대 닉네임, 내 닉네임, 게임 계정 정보\n'
                '· 보내는 사람은 이름 대신 무작위 번호로만 구분돼요\n\n'
                '[설정] 메뉴에서 언제든 끄거나 켤 수 있어요.')
            share_data.set_consent(ok)
            self.share_on.set(ok)
        self.share_loop()

    def toggle_share(self):
        import share_data
        share_data.set_consent(self.share_on.get())
        self.log('매주 경기 기록 보내기를 ' + ('켰어요. (닉네임 제외)' if self.share_on.get() else '껐어요.'))
        if self.share_on.get():
            self.share_loop(once=True)

    def share_loop(self, once=False):
        """끝난 주 기록이 있으면 보냄 (뒤에서 조용히). 켜 두면 6시간마다 다시 확인"""
        def work():
            try:
                import share_data
                share_data.send_pending(self.log)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()
        if not once:
            self.root.after(6 * 3600 * 1000, self.share_loop)

    def check_update(self):
        def work():
            import updater
            try:
                rel = updater.check(self.log)
            except Exception as e:
                self.log(f'업데이트 확인 실패: {e}', 'bad')
                return
            if rel:
                self.log(f"새 버전 v{rel['version']}이 있어요. 프로그램을 껐다 켜면 자동으로 바뀌어요.", 'good')
            else:
                self.log(f'지금 버전 v{self.version}이 최신이에요.')
        self.nb.select(self._log_tab)
        threading.Thread(target=work, daemon=True).start()

    def _open_dir(self, d):
        os.makedirs(d, exist_ok=True)
        self._open(d)

    def get_game(self):
        if self.game is None:
            from game_adb import AdbGame
            self.game = AdbGame(SC.SET.get('adb_경로') or None, SC.SET.get('adb_기기') or None, self.log)
        self.game.stop_flag = False
        self.game.find()
        if not getattr(self, '_conn_logged', False):
            self._conn_logged = True
            w, h = self.game.screen_size()
            self.log(f'  [연결] 에뮬레이터 {self.game.device} / 화면 {w}x{h} / adb: {self.game.adb}')
            dev = self.game.device
            self.ui(lambda: self.conn_txt.set(f'에뮬레이터: {dev} ({w}×{h})'))
        return self.game

    def run_bg(self, title, fn):
        if self.busy:
            return
        self.set_busy(True, title + ' 중...')
        self.log('')
        self.log(f'■ {title}', 'head')

        def work():
            try:
                fn()
            except StopRequested:
                self.log('중지했어요.', 'bad')
            except FlowError as e:
                self.log('⚠ ' + str(e), 'bad')
                self.claude_explain(title, str(e))
            except Exception as e:
                self.log('⚠ 예상하지 못한 오류: ' + str(e), 'bad')
                self.log(traceback.format_exc())
            finally:
                self.ui(lambda: (self.set_busy(False), self.refresh_learn_count()))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ Claude Code 연동
    def toggle_claude(self):
        if not self.claude_on.get():
            self.helper = None
            self.claude_txt.set('Claude 연동: 꺼짐')
            self.log('Claude Code 연동을 껐어요.')
            return
        if self.busy:
            self.claude_on.set(False)
            return
        from claude_link import ClaudeLink, ClaudeError, find_claude
        if not find_claude():
            self.claude_on.set(False)
            messagebox.showwarning('클로드 코드 연동', 'PC에서 Claude Code(claude)를 찾지 못했어요.\n'
                                   '설명서.txt 8번의 설치 방법을 따라 설치하고 로그인한 뒤 다시 체크해 주세요.')
            return

        def fn():
            link = ClaudeLink(self.log)
            self.log(f'  Claude Code: {link.exe}')
            try:
                v, ok = link.check()
            except ClaudeError as e:
                self.log('⚠ ' + str(e), 'bad')
                self.ui(lambda: self.claude_on.set(False))
                return
            self.log(f'  버전 {v} / 응답 확인 {"OK" if ok else "이상함"} ({link.last_secs:.0f}초)', 'good' if ok else 'bad')
            if ok:
                self.helper = link
                self.ui(lambda: self.claude_txt.set('Claude 연동: 켜짐'))
                self.log('  이제 확실하지 않은 값은 Claude가 화면을 보고 바로잡고, 화면이 막히면 Claude가 어디를 누를지 알려줘요.')
                self.log('  (Claude 구독 사용량을 쓰고, 한 번 물어볼 때 30초~2분 정도 걸려요)')
            else:
                self.ui(lambda: self.claude_on.set(False))
        self.run_bg('클로드 코드 연결 확인', fn)

    def claude_explain(self, what, err):
        # 작업이 막혀서 멈췄을 때 Claude에게 지금 화면 설명을 받음
        if not (self.helper and self.game):
            return
        if any(k in err for k in ('먼저 [① 내 덱', '타순이 비어', '기록할 분석이 없어요')):
            return                                     # 화면 문제가 아닌 안내 메시지
        import claude_help as CH
        from claude_link import ClaudeError
        try:
            self.log('  → Claude에게 지금 화면을 보여 주고 물어볼게요...')
            data, fn = CH.explain_screen(self.helper, self.game.grab(), what, err)
            self.log(f"  [Claude] 화면: {data.get('screen')}", 'head')
            if data.get('why'):
                self.log(f"  [Claude] 막힌 이유: {data.get('why')}")
            if data.get('advice'):
                self.log(f"  [Claude] 이렇게 해 보세요: {data.get('advice')}", 'good')
            CH.write_memo('작업 멈춤', what, '', [err], [], ' / '.join(str(x) for x in (data.get('screen'), data.get('why'), data.get('advice')) if x), fn)
        except Exception as e:
            self.log(f'  ⚠ Claude 도움 실패: {e}')

    def do_claude_learn(self):
        if self.busy:
            return
        from claude_link import find_claude
        if not find_claude():
            messagebox.showwarning('Claude 일괄 학습', 'PC에서 Claude Code(claude)를 찾지 못했어요.\n'
                                   '설명서.txt 8번의 설치 방법을 확인해 주세요.')
            return
        import claude_ui
        self.set_busy(True, 'Claude 일괄 학습 중...')

        def done():
            self.set_busy(False)
            self.refresh_learn_count()
        claude_ui.run_batch_learning(self.root, self.log, self.ui, on_done=done, link=self.helper)

    def do_import_excel(self):
        if self.busy:
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(title='고친 엑셀 파일 고르기', initialdir=ST.ROOT_DIR,
                                          initialfile=os.path.basename(ST.XLSX),
                                          filetypes=[('엑셀 파일', '*.xlsx'), ('모든 파일', '*.*')])
        if not path:
            return
        if not messagebox.askyesno('엑셀 수정 반영',
                                   '이 엑셀에서 고친 내용을 원본 기록에 반영할까요?\n\n'
                                   '· 엑셀에서 먼저 [저장]했는지 확인해 주세요.\n'
                                   '· 스탯/스킬/잠재/좌우를 고친 경기는 점수와 점수차를 다시 계산해요.\n'
                                   '· 바꾸기 전 원본은 기록데이터\\백업_날짜 폴더에 복사해 둬요.'):
            return

        recalc_all = messagebox.askyesno('엑셀 수정 반영',
                                         '고치지 않은 경기도 지금 스킬 점수표와 설정으로 점수를 전부 다시 계산할까요?\n'
                                         '(스킬 점수표를 바꿨거나 프로그램을 새 버전으로 바꿨을 때 [예])')

        def fn():
            import excel_import as EI
            try:
                s = EI.apply_excel(path, self.log, recalc_all=recalc_all)
            except RuntimeError as e:
                self.log('⚠ ' + str(e), 'bad')
                return
            n = s['match_cells'] + s['player_cells'] + s['deck_cells']
            if not n:
                self.log('원본과 다른 칸이 없어요. (엑셀에서 [저장]을 눌렀는지 확인해 주세요)')
            else:
                self.log(f"반영 완료: 매치기록 {s['match_cells']}칸, 선수기록 {s['player_cells']}칸, 내 덱 {s['deck_cells']}칸 / "
                         f"점수 다시 계산 {s['recalc']}경기", 'good')
                self.log(f"  바꾸기 전 원본: {s.get('backup')}")
            for t in s['notes']:
                self.log('  ⚠ ' + t, 'bad')
            if s.get('excel_error'):
                self.log('⚠ ' + s['excel_error'] + ' (반영은 끝났어요. 엑셀을 닫고 [기록 엑셀 열기]를 누르면 새로 만들어져요)', 'bad')
        self.run_bg('엑셀 수정 반영', fn)

    # ------------------------------------------------------------ 자동 배치 / 자동 진행
    def do_arrange(self):
        def fn():
            import auto as A
            pending = ST.load_json(ST.PENDING)
            if not pending:
                raise FlowError('먼저 [② 상대 분석]을 해 주세요.')
            if any(pending.get('recorded') or []):
                raise FlowError('이미 결과를 기록한 분석이에요. [② 상대 분석]을 다시 해 주세요.')
            g = self.get_game()
            r = Runner(g, self.log, self.helper)
            if R.which_screen(g.grab()) != 'lobby':
                raise FlowError('랭킹 챌린지 [로비] 화면에서 눌러 주세요.')
            A.AutoRunner(r, self.log).arrange(pending)
        self.run_bg('추천대로 배치', fn)

    def do_auto(self):
        if self.busy:
            return
        o = getattr(self, 'auto_opts', None) or {'mode': 'point', 'star_cap': 0, 'deadline': SC.SET.get('자동_마감시각', '23:59'),
                                                  'cycles': 0}
        w = tk.Toplevel(self.root)
        w.title('자동 진행')
        w.grab_set()
        pad = {'padx': 10, 'pady': 4}
        ttk.Label(w, text='상대 분석 → 추천대로 배치 → 연속 경기 → 결과 기록 → 갱신 을 반복해요.\n'
                          '랭킹 챌린지 [로비] 화면을 열어 두고 시작하세요. 멈추려면 F12 또는 [■ 중지].',
                  font=FONT).pack(anchor='w', **pad)
        mode = tk.StringVar(value=o['mode'])
        f1 = ttk.LabelFrame(w, text='갱신 범위')
        f1.pack(fill='x', **pad)
        ttk.Radiobutton(f1, text='포인트 갱신까지 (다음 갱신이 스타가 되면 남은 5경기는 남겨 두고 멈춤)',
                        variable=mode, value='point').pack(anchor='w', padx=6, pady=2)
        f2 = ttk.Frame(f1)
        f2.pack(anchor='w', padx=6, pady=2)
        ttk.Radiobutton(f2, text='스타 갱신까지, 오늘 최대', variable=mode, value='star').pack(side='left')
        cap = tk.Spinbox(f2, from_=1, to=12, width=4)
        cap.delete(0, 'end')
        cap.insert(0, str(o['star_cap'] or 6))
        cap.pack(side='left', padx=4)
        ttk.Label(f2, text='번 (다 쓰면 마지막 5경기는 남겨 두고 멈춤)').pack(side='left')
        f3 = ttk.Frame(w)
        f3.pack(fill='x', **pad)
        ttk.Label(f3, text='마감 시각').pack(side='left')
        dl = ttk.Entry(f3, width=7)
        dl.insert(0, o['deadline'])
        dl.pack(side='left', padx=4)
        ttk.Label(f3, text='   최대 바퀴 수 (0 = 끝까지)').pack(side='left')
        cy = tk.Spinbox(f3, from_=0, to=30, width=4)
        cy.delete(0, 'end')
        cy.insert(0, str(o['cycles']))
        cy.pack(side='left', padx=4)
        ttk.Label(w, text='※ 스타는 위에서 정한 횟수만큼만 써요. 구매 버튼과 [연속 경기 종료]는 누르지 않아요.\n'
                          '※ 처음 써 볼 때는 최대 바퀴 수를 1로 두고 한 바퀴만 지켜보는 걸 추천해요.',
                  foreground='#555').pack(anchor='w', **pad)

        def start():
            try:
                h, m = (int(x) for x in dl.get().strip().split(':'))
                assert 0 <= h < 24 and 0 <= m < 60
                c = int(cap.get())
                n = int(cy.get())
            except Exception:
                messagebox.showwarning('자동 진행', '마감 시각은 23:59 처럼, 횟수는 숫자로 적어 주세요.', parent=w)
                return
            if mode.get() == 'star' and not messagebox.askyesno(
                    '자동 진행', f'스타 갱신을 오늘 최대 {c}번까지 써요. (오늘 이 프로그램으로 이미 쓴 횟수 포함)\n시작할까요?', parent=w):
                return
            self.auto_opts = {'mode': mode.get(), 'star_cap': c, 'deadline': f'{h:02d}:{m:02d}', 'cycles': n}
            w.destroy()

            def fn():
                import auto as A
                r = Runner(self.get_game(), self.log, self.helper)
                A.AutoRunner(r, self.log, mode=self.auto_opts['mode'],
                             star_cap=self.auto_opts['star_cap'] if self.auto_opts['mode'] == 'star' else 0,
                             deadline=self.auto_opts['deadline'], max_cycles=self.auto_opts['cycles'],
                             show=lambda res: self.show_analysis(res, switch=False)).run()
            self.run_bg('자동 진행', fn)
        bf = ttk.Frame(w)
        bf.pack(pady=8)
        ttk.Button(bf, text='시작', command=start, style='Primary.TButton').pack(side='left', padx=4)
        ttk.Button(bf, text='취소', command=w.destroy).pack(side='left', padx=4)

    def open_memo(self):
        import claude_help as CH
        if not os.path.isfile(CH.MEMO):
            messagebox.showinfo('Claude 진단 메모', '아직 메모가 없어요. [클로드 코드 연동]을 켜고 분석하면 Claude의 확인 결과와 의견이 여기에 쌓여요.')
            return
        self._open(CH.MEMO)

    def do_claude_audit(self):
        if self.busy:
            return
        from claude_link import find_claude
        if not find_claude():
            messagebox.showwarning('학습 내용 점검', 'PC에서 Claude Code(claude)를 찾지 못했어요.\n'
                                   '설명서.txt 8번의 설치 방법을 확인해 주세요.')
            return
        import claude_ui
        self.set_busy(True, 'Claude 학습 내용 점검 중...')

        def done():
            self.set_busy(False)
        claude_ui.run_audit(self.root, self.log, self.ui, on_done=done, link=self.helper)

    def stop(self):
        if self.helper:
            self.helper.stop()
        if self.game:
            self.game.stop_flag = True

    def _open(self, path):
        if sys.platform == 'win32':
            os.startfile(path)
        else:
            subprocess.Popen(['xdg-open', path])

    def open_excel(self):
        err = ST.rebuild_excel()                        # 열기 전에 원본 기록으로 새로 만듦 (열려 있으면 그대로 엶)
        if err and not os.path.isfile(ST.XLSX):
            messagebox.showwarning('엑셀', err)
            return
        self._open(ST.XLSX)

    def do_diag(self):
        """지금 게임 화면을 저장 (문제 생겼을 때 이 파일을 보내 주세요)"""
        def fn():
            g = self.get_game()
            x, y, w, h = g.client_rect()
            img = g.grab()
            d = os.path.join(ST.ROOT_DIR, '진단')
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, ST.now().replace(':', '').replace(' ', '_') + '.png')
            img.save(path)
            self.log(f'저장: {path}')
            self.log(f'  창 안쪽 크기 {w}x{h} (기준 918x1633), 인식된 화면: {R.which_screen(img) or "모름"}')
        self.run_bg('진단용 캡처', fn)

    # ------------------------------------------------------------ ① 내 덱
    def do_deck(self):
        def fn():
            r = Runner(self.get_game(), self.log, self.helper)
            deck = r.read_my_deck()
            Runner.save_deck(deck)
            self.log('읽기 완료. 타순을 확인해 주세요.', 'good')
            self.ui(lambda: self.order_dialog(deck))
        self.run_bg('내 덱 읽기', fn)

    def do_order(self):
        deck = ST.load_json(ST.MY_DECK)
        if not deck:
            messagebox.showinfo('타순 입력', '먼저 [① 내 덱 읽기]를 해 주세요.')
            return
        self.order_dialog(deck)

    def order_dialog(self, deck):
        w = tk.Toplevel(self.root)
        w.title('타순 입력')
        w.grab_set()
        ttk.Label(w, text='각 타자의 타순(1~9)을 골라 주세요. 지난번 값은 미리 채워져 있어요.',
                  padding=8).grid(row=0, column=0, columnspan=3)
        vars_ = []
        for i, b in enumerate(deck['batters']):
            ttk.Label(w, text=b.get('pos') or '', width=5).grid(row=i + 1, column=0, padx=6)
            ttk.Label(w, text=b.get('name') or '', width=18).grid(row=i + 1, column=1)
            v = tk.StringVar(value=str(b.get('slot') or ''))
            ttk.Combobox(w, textvariable=v, values=[str(k) for k in range(1, 10)], width=4,
                         state='readonly').grid(row=i + 1, column=2, padx=6, pady=2)
            vars_.append(v)
        ttk.Separator(w).grid(row=20, column=0, columnspan=3, sticky='ew', pady=6)
        sp = ', '.join(f"{p.get('rotation')}선발 {p.get('name')}" for p in deck['pitchers'])
        ttk.Label(w, text='선발: ' + sp, wraplength=420).grid(row=21, column=0, columnspan=3, padx=6)

        def save():
            try:
                slots = [int(v.get()) for v in vars_]
            except ValueError:
                messagebox.showwarning('타순 입력', '빈 칸이 있어요.', parent=w)
                return
            if sorted(slots) != list(range(1, 10)):
                messagebox.showwarning('타순 입력', '1~9번이 한 번씩만 들어가야 해요. (겹치거나 빠진 번호가 있어요)', parent=w)
                return
            for b, s in zip(deck['batters'], slots):
                b['slot'] = s
            err = Runner.save_deck(deck)
            self.log('내 덱과 타순을 저장했어요.', 'good')
            if err:
                self.log('⚠ ' + err, 'bad')
            self.show_deck(deck)
            w.destroy()
        ttk.Button(w, text='저장', command=save, style='Primary.TButton').grid(row=22, column=0, columnspan=3, pady=8)

    def show_deck(self, deck):
        """내 덱을 [내 덱] 탭에 표로 보여 줌 (작업 스레드에서 불러도 됨)"""
        self.log('  내 덱을 [내 덱] 탭에 정리했어요.')
        self.ui(lambda: (self._render_deck(deck), self.nb.select(2)))

    def _render_deck(self, deck):
        t = self.deck_txt
        t.configure(state='normal')
        t.delete('1.0', 'end')
        t.insert('end', '내 타자   (평균 조건 보정점수)\n', 'title')
        t.insert('end', '  ' + wpad('타순', 6) + wpad('포지션', 8) + wpad('이름', 14) + wpad('점수', 7, True) + '   스킬\n', 'muted')
        for b in sorted(deck['batters'], key=lambda x: x.get('slot') or 99):
            sk = ', '.join(f'{n} {l}' for n, l in b.get('skills') or [])
            t.insert('end', '  ' + wpad(b.get('slot') or '-', 6) + wpad(b.get('pos') or '', 8) + wpad(b.get('name') or '', 14)
                     + f"{b.get('avg_score') or 0:7.1f}   {sk}\n")
        t.insert('end', '\n내 선발\n', 'title')
        t.insert('end', '  ' + wpad('순서', 6) + wpad('투', 8) + wpad('이름', 14) + wpad('점수', 7, True) + '   스킬\n', 'muted')
        for p in deck['pitchers']:
            sk = ', '.join(f'{n} {l}' for n, l in p.get('skills') or [])
            hand = {'L': '좌', 'R': '우'}.get(p.get('throw'), '?')
            t.insert('end', '  ' + wpad(str(p.get('rotation') or '') + '선발', 6) + wpad(hand, 8) + wpad(p.get('name') or '', 14)
                     + f"{p.get('avg_score') or 0:7.1f}   {sk}\n")
        if deck.get('saved_at') or deck.get('time'):
            t.insert('end', f"\n읽은 시각 {deck.get('saved_at') or deck.get('time')}\n", 'muted')
        t.configure(state='disabled')

    # ------------------------------------------------------------ ② 분석
    def do_analyze(self):
        def fn():
            r = Runner(self.get_game(), self.log, self.helper)
            res = r.analyze()
            if res.get('has_results'):
                self.log('로비에 이미 경기 결과가 있어요. 먼저 결과를 기록할게요.')
                out = r.record_results()
                self.log(out['msg'], 'good')
                if out.get('excel_error'):
                    self.log('⚠ ' + out['excel_error'], 'bad')
                self.log('이제 게임에서 [갱신]을 누른 뒤 다시 [② 상대 분석]을 눌러 주세요.')
                return
            self.show_analysis(res)
        self.run_bg('상대 분석', fn)

    def show_analysis(self, res, switch=True):
        """분석 결과를 [추천 배치] 표와 [상세표]에 보여 주고, [진행 기록]에는 요약만 남김 (작업 스레드에서 불러도 됨)"""
        a = build_analysis(res)
        self.log('  ▶ 추천 배치 (기대 승점이 가장 큰 배치)', 'head')
        for line, tag in a['log']:
            self.log(line, tag)

        def render():
            tv = self.rec_tv
            tv.delete(*tv.get_children())
            for r in a['rows']:
                tv.insert('', 'end', values=r['values'], tags=(r['tag'],) + (('chg',) if r['values'][-1] else ()))
            tv.configure(height=max(5, len(a['rows'])))
            self.rec_title.configure(text=a['title'])
            self.rec_sub.configure(text=a['sub'])
            self.rec_sum.configure(text=a['sum'])
            self.rec_note.configure(text=a['note'])
            self.rec_warn.configure(text='\n'.join(a['warns']), bg=C['warn_bg'] if a['warns'] else C['card'],
                                    padx=10 if a['warns'] else 0, pady=8 if a['warns'] else 0)
            d = self.detail
            d.configure(state='normal')
            d.delete('1.0', 'end')
            for line, tag in a['detail']:
                d.insert('end', line + '\n', tag or ())
            d.configure(state='disabled')
            if switch:
                self.nb.select(0)
        self.ui(render)

    # ------------------------------------------------------------ ③ 결과
    def do_record(self):
        def fn():
            r = Runner(self.get_game(), self.log, self.helper)
            out = r.record_results()
            self.log(out['msg'], 'good' if out['recorded'] else None)
            if out.get('excel_error'):
                self.log('⚠ ' + out['excel_error'], 'bad')
            if out.get('left'):
                self.log(f"  아직 결과가 없는 경기 {out['left']}개는 경기 후 다시 눌러 주세요.")
        self.run_bg('결과 기록', fn)

    # ------------------------------------------------------------ 학습 대기
    def pending_items(self):
        if not os.path.isdir(R.PENDING_DIR):
            return []
        return sorted(f[:-5] for f in os.listdir(R.PENDING_DIR) if f.endswith('.json'))

    def refresh_learn_count(self):
        n = len(self.pending_items())
        self.learn_txt.set(f'학습 대기 {n}건')
        try:
            self.learn_menu.entryconfigure(0, label=f'학습 대기 확인 ({n}건)')
        except Exception:
            pass

    def do_learn(self):
        items = self.pending_items()
        if not items:
            messagebox.showinfo('학습 대기', '확인할 항목이 없어요.')
            return
        w = tk.Toplevel(self.root)
        w.title('학습 대기 확인')
        w.grab_set()
        KIND = {'grade': '잠재 등급 (예: SR+, B, C+)', 'level': '스킬 레벨 (숫자)', 'ovr_digit': '카드 OVR 숫자 한 글자',
                'angle_digit': '발사각 숫자 한 글자', 'card_type': '카드 종류 (' + '/'.join(R.CARD_TYPES) + ')',
                'label_char': '라벨 글자 (1~5 또는 L/R)'}
        state = {'i': 0}
        img_lbl = ttk.Label(w)
        img_lbl.pack(padx=10, pady=10)
        info = ttk.Label(w, font=FONT)
        info.pack()
        ent = ttk.Entry(w, font=('맑은 고딕', 14), width=10, justify='center')
        ent.pack(pady=6)
        cnt = ttk.Label(w)
        cnt.pack()

        def show():
            if state['i'] >= len(items):
                R.store().reload()
                R._UI_CACHE.clear()
                self.refresh_learn_count()
                w.destroy()
                messagebox.showinfo('학습 대기', '끝났어요. 다음 읽기부터 반영돼요.')
                return
            key = items[state['i']]
            meta = json.load(open(os.path.join(R.PENDING_DIR, key + '.json'), encoding='utf-8'))
            im = Image.open(os.path.join(R.PENDING_DIR, key + '.png')).convert('RGB')
            sc = max(1, min(8, 240 // max(im.width, im.height, 1)))
            tkim = ImageTk.PhotoImage(im.resize((im.width * sc, im.height * sc), Image.NEAREST))
            img_lbl.configure(image=tkim)
            img_lbl.image = tkim
            info.configure(text=f"{KIND.get(meta['kind'], meta['kind'])}\n위치: {meta.get('context', '')}\n프로그램 추측: {meta.get('guess')}")
            ent.delete(0, 'end')
            ent.insert(0, meta.get('guess') or '')
            ent.focus_set()
            cnt.configure(text=f"{state['i'] + 1} / {len(items)}")
            state['meta'], state['key'], state['img'] = meta, key, im

        def remove():
            for ext in ('.png', '.json'):
                try:
                    os.remove(os.path.join(R.PENDING_DIR, state['key'] + ext))
                except OSError:
                    pass

        def save(_=None):
            ans = ent.get().strip().upper().replace(' ', '')
            kind = state['meta']['kind']
            if kind == 'card_type':
                ans = ent.get().strip()
                if ans not in R.CARD_TYPES:
                    messagebox.showwarning('학습', '카드 종류는 ' + '/'.join(R.CARD_TYPES) + ' 중 하나로 적어 주세요.', parent=w)
                    return
                p = os.path.join(R.TEMPLATE_DIR, 'ui', 'wm_' + ans + '.png')
                if not os.path.isfile(p):
                    im = state['img']
                    im.crop((8, 8, im.width - 8, im.height - 8)).save(p)
            else:
                ok = {'grade': ans in R.GRADES or ans == '-',
                      'level': ans.isdigit() and 1 <= int(ans) <= 10,
                      'ovr_digit': len(ans) == 1 and ans.isdigit(),
                      'angle_digit': len(ans) == 1 and ans.isdigit(),
                      'label_char': ans in ('1', '2', '3', '4', '5', 'L', 'R', 'S')}.get(kind, False)
                if not ok:
                    messagebox.showwarning('학습', '입력 형식이 맞지 않아요.', parent=w)
                    return
                if ans != '-':
                    R.store().add(kind, ans, state['img'])
            remove()
            state['i'] += 1
            show()

        def skip():
            remove()
            state['i'] += 1
            show()
        bf = ttk.Frame(w)
        bf.pack(pady=8)
        ttk.Button(bf, text='정답 저장 (Enter)', command=save, style='Primary.TButton').pack(side='left', padx=4)
        ttk.Button(bf, text='모르겠음 (버리기)', command=skip).pack(side='left', padx=4)
        w.bind('<Return>', save)
        show()


def _w(ch):
    import unicodedata
    return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1


def wpad(text, width, right=False):
    """한글은 고정폭 글꼴에서 영문 두 칸을 차지해서, 칸 수를 화면 폭 기준으로 맞춤 (표 줄이 삐뚤어지지 않게)"""
    text = str(text)
    out, n = '', 0
    for ch in text:
        if n + _w(ch) > width:
            break
        out += ch
        n += _w(ch)
    sp = ' ' * (width - n)
    return sp + out if right else out + sp


def build_analysis(res):
    """분석 결과(res)를 화면에 보여 줄 모양으로 정리. 화면(tkinter)과 상관없는 순수 계산이라 따로 시험할 수 있음"""
    p, M, best = res['pending'], res['matrix'], res['best']
    deck = p['deck']
    names = [f"{q.get('rotation')}선발 {q.get('name')}" for q in deck['pitchers']]
    nop = len(p['opps'])
    wm = res.get('model') or {}
    has_p = 'p_win' in M[0][0]
    out = {'rows': [], 'log': [], 'detail': [], 'warns': []}
    D = out['detail']

    # ---- 추천 표
    cur_ep, cur_all, changes = 0.0, True, 0
    for r in p['recommend']:
        j = r.get('col', r['row'] - 1)
        o = p['opps'][j]
        rec, cur = r['recommend'], r['current']
        c_rec = M[rec - 1][j]
        pw = f"{c_rec['p_win'] * 100:.0f}%" if 'p_win' in c_rec else ''
        cur_txt = '못 읽음'
        if cur:
            c_cur = M[cur - 1][j]
            cur_ep += c_cur.get('exp_pts', 0)
            cur_txt = f"{cur}선발 {c_cur['diff']:+.1f}" + (f" · {c_cur['p_win'] * 100:.0f}%" if 'p_win' in c_cur else '')
        else:
            cur_all = False
        chg = bool(cur and cur != rec)
        changes += chg
        hand = {'L': '좌', 'R': '우'}.get(o['sp'].get('throw'), '?')
        out['rows'].append({'values': (r['row'], o['lobby'].get('nick', ''), f"{hand}투 {o['sp'].get('name', '')}",
                                       f"{rec}선발 {deck['pitchers'][rec - 1].get('name', '')}", f"{c_rec['diff']:+.1f}",
                                       pw, cur_txt, '변경' if chg else ''),
                            'tag': 'good' if c_rec['diff'] >= 0 else 'bad'})
        out['log'].append((f"  줄{r['row']} {o['lobby'].get('nick', '')[:10]:<10} 상대선발 {hand}투 {o['sp'].get('name', '')[:8]:<8}"
                           f" → 내 {rec}선발 {deck['pitchers'][rec - 1].get('name', '')[:8]} {c_rec['diff']:+.1f}"
                           + (f' 승률 {pw}' if pw else '') + (f'   (지금: {cur_txt})' if cur else '   (지금 배치: 못 읽음)')
                           + ('  ← 변경' if chg else ''), 'good' if c_rec['diff'] >= 0 else 'bad'))
    n_games = len(p['recommend'])
    out['title'] = '추천 배치' + (f' — {changes}줄 바꾸기' if changes else (' — 지금 배치 그대로' if cur_all else ''))
    if has_p:
        p0 = 1 / (1 + math.exp(-wm.get('a', 0)))
        src = (f"기록 {wm.get('n')}경기로 보정" if wm.get('n') else '기본값') if wm.get('auto') else '설정값'
        out['sub'] = f"예상 승률: 점수차 0이면 {p0 * 100:.0f}%, 10점당 약 {wm.get('k', 0) * 250:.0f}%p ({src})   ·   초록 = 내가 유리, 빨강 = 불리"
    else:
        out['sub'] = '초록 = 내가 유리, 빨강 = 불리'
    if 'exp_pts' in M[0][0]:
        out['sum'] = (f"기대 승점  추천 {best[0]:.2f}점 (경기당 {best[0] / max(1, n_games):.2f})"
                      + (f"   ·   지금 배치 {cur_ep:.2f}점" if cur_all and cur_ep else ''))
        bs = res.get('best_sum')
        note = ''
        if bs and list(bs[1]) != list(best[1]):
            alt = ', '.join(f"줄{r['row']}={r.get('recommend_sum')}" for r in p['recommend'])
            note = (f'점수차 합만 보면 {alt} 이지만, 이미 크게 이기거나 지는 경기보다 '
                    '팽팽한 경기에 강한 선발을 넣는 쪽이 기대 승점이 더 커요.')
        out['note'] = note
    else:
        out['sum'] = f'점수차 합계: 추천 {best[0]:+.1f}'
        out['note'] = ''
    out['log'].append(('  ' + out['sum'], None))
    miss = [k for k in range(1, 6) if k not in {o.get('row') for o in p['opps']}]
    if miss:
        out['warns'].append(f'⚠ 줄{miss} 상대는 못 읽어서 빠졌어요. 그 줄은 남는 선발을 넣거나, 다시 [② 상대 분석]을 눌러 주세요.')
    warns = [(o['row'], pl.get('name'), pl['warn']) for o in p['opps'] for pl in [o['sp']] + o['batters'] if pl.get('warn')]
    if warns:
        out['warns'].append(f'⚠ 확실하지 않은 값 {len(warns)}건 → [학습] 메뉴의 [학습 대기 확인]에서 정답을 알려 주면 다음부터 정확해져요.')
    if res.get('excel_error'):
        out['warns'].append('⚠ ' + res['excel_error'])
    for w in out['warns']:
        out['log'].append(('  ' + w, 'bad'))

    # ---- 상세표 (예전 글자 표 그대로)
    hdr = '  ' + ' ' * 18 + ''.join(wpad('상대' + str(o.get('row', j + 1)), 9, True) for j, o in enumerate(p['opps']))
    D.append(('점수차 표   (+면 내가 유리 · 행 = 내 선발, 열 = 상대 로비 줄 순서)', 'title'))
    D.append((hdr, 'muted'))
    for i, nm in enumerate(names):
        D.append(('  ' + wpad(nm, 18) + ''.join(f'{M[i][j]["diff"]:>+9.1f}' for j in range(nop)), None))
    if has_p:
        D.append(('', None))
        D.append(('예상 승률 표 (%)', 'title'))
        D.append((hdr, 'muted'))
        for i, nm in enumerate(names):
            D.append(('  ' + wpad(nm, 18) + ''.join(f'{M[i][j]["p_win"] * 100:>8.0f}%' for j in range(nop)), None))
    D.append(('', None))
    D.append(('선발별 상세 — 같은 선발이라도 상대에 따라 점수가 바뀌는 부분', 'title'))
    D.append(('  [내 선발 점수]  괄호 = 그 선발의 평균 점수 대비 (좌/우타킬러, 좌승사자, 패기 등이 상대 타선에 따라 바뀜)', 'muted'))
    D.append((hdr + wpad('평균', 9, True), 'muted'))
    for i, nm in enumerate(names):
        avg = deck['pitchers'][i].get('avg_score')
        cells = ''
        for j in range(nop):
            v = M[i][j]['my_sp']
            cells += f'{v:>6.1f}' + (f'({v - avg:+.0f})' if avg is not None else '   ')
        D.append(('  ' + wpad(nm, 18) + cells + (f'{avg:>9.1f}' if avg is not None else ''), None))
    D.append(('', None))
    D.append(('  [상대 타선 점수]  내 이 선발을 상대할 때 (상대 타자의 좌/우완킬러, 좌타해결사 등이 내 선발 손에 따라 바뀜)', 'muted'))
    D.append((hdr, 'muted'))
    for i, nm in enumerate(names):
        D.append(('  ' + wpad(nm, 18) + ''.join(f'{M[i][j]["opp_lineup"]:>9.1f}' for j in range(nop)), None))
    D.append(('', None))
    D.append(('  [내 선발과 거의 상관없는 부분]', 'muted'))
    D.append(('  ' + wpad('내 타선 점수', 18) + ''.join(f'{M[0][j]["my_lineup"]:>9.1f}' for j in range(nop)), None))
    D.append(('  ' + wpad('상대 선발 점수', 18) + ''.join(f'{M[0][j]["opp_sp"]:>9.1f}' for j in range(nop)), None))
    return out


def main():
    from game import set_dpi_aware
    set_dpi_aware()
    root = tk.Tk()
    try:
        setup_style(root)
    except Exception:
        traceback.print_exc()
    app = App(root)
    root.after(3000, app.after_start)
    root.mainloop()


if __name__ == '__main__':
    main()

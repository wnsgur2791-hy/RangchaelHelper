# -*- coding: utf-8 -*-
"""Claude 일괄 학습 화면 (메인 프로그램 버튼과 클로드학습.bat 양쪽에서 씀)"""
import threading, traceback
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

import recognize as R
import claude_help as CH
from claude_link import ClaudeLink, ClaudeError

KIND_KO = {'grade': '잠재 등급', 'level': '스킬 레벨', 'ovr_digit': 'OVR 숫자 1글자', 'angle_digit': '발사각 숫자 1글자',
           'card_type': '카드 종류', 'label_char': '라벨 글자'}


def run_batch_learning(root, log, call, on_done=None, link=None):
    """call(fn): fn을 화면 쪽(메인 스레드)에서 실행하도록 넘기는 함수"""
    """학습대기 전부를 Claude에게 보내고, 받은 정답을 확인 창으로 보여 줌"""
    items = CH.load_pending()
    if not items:
        messagebox.showinfo('Claude 일괄 학습', '학습 대기 중인 그림이 없어요.', parent=root)
        if on_done:
            on_done()
        return
    log('')
    log(f'■ Claude 일괄 학습 — {len(items)}개')

    def work():
        try:
            lk = link or ClaudeLink(log)
            answers = CH.label_pending(lk, items, log)
            n_ok = sum(1 for v in answers.values() if v)
            log(f'  Claude 답 {n_ok}/{len(items)}개 받음 → 확인 창에서 보고 [모두 저장]을 눌러 주세요.')
            call(lambda: ReviewWindow(root, items, answers, log, on_done))
        except ClaudeError as e:
            log('⚠ ' + str(e))
            if on_done:
                call(on_done)
        except Exception as e:
            log('⚠ 예상하지 못한 오류: ' + str(e))
            log(traceback.format_exc())
            if on_done:
                call(on_done)
    threading.Thread(target=work, daemon=True).start()


class ReviewWindow:
    """Claude가 붙인 정답을 한눈에 보고 고친 뒤 한꺼번에 저장"""

    def __init__(self, root, items, answers, log, on_done=None):
        self.items, self.log, self.on_done = items, log, on_done
        w = self.w = tk.Toplevel(root)
        w.title('Claude 일괄 학습 — 정답 확인')
        w.geometry('620x700')
        ttk.Label(w, text='Claude가 읽은 정답이에요. 틀린 칸은 고치고, 모르겠으면 비워 두세요 (비운 칸은 학습대기에 남아요).',
                  wraplength=580).pack(padx=10, pady=(10, 4), anchor='w')
        outer = ttk.Frame(w)
        outer.pack(fill='both', expand=True, padx=10)
        cv = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient='vertical', command=cv.yview)
        inner = ttk.Frame(cv)
        inner.bind('<Configure>', lambda e: cv.configure(scrollregion=cv.bbox('all')))
        cv.create_window((0, 0), window=inner, anchor='nw')
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        cv.bind_all('<MouseWheel>', lambda e: cv.yview_scroll(int(-e.delta / 120), 'units'))
        self.rows = []
        self.photos = []
        for i, it in enumerate(items):
            im = it['img']
            sc = max(1, min(4, 60 // max(1, im.height)))
            if it['kind'] == 'card_type':
                sc = 1
            if it['kind'] in ('ovr_digit', 'angle_digit', 'label_char'):
                from PIL import ImageOps
                im = ImageOps.expand(ImageOps.invert(im.convert('L')), border=3, fill=255)
            ph = ImageTk.PhotoImage(im.resize((im.width * sc, im.height * sc), Image.NEAREST))
            self.photos.append(ph)
            ttk.Label(inner, image=ph).grid(row=i, column=0, padx=4, pady=3, sticky='w')
            ttk.Label(inner, text=f"{KIND_KO.get(it['kind'], it['kind'])}\n추측: {it['guess'] or '-'}",
                      font=('맑은 고딕', 9)).grid(row=i, column=1, padx=6, sticky='w')
            e = ttk.Entry(inner, width=8, font=('맑은 고딕', 13), justify='center')
            ans = answers.get(it['key'])
            if ans:
                e.insert(0, ans)
            e.grid(row=i, column=2, padx=6)
            mark = '' if ans else '← Claude도 모름'
            ttk.Label(inner, text=mark, foreground='#c0262d').grid(row=i, column=3, sticky='w')
            self.rows.append((it, e))
        bf = ttk.Frame(w)
        bf.pack(pady=8)
        ttk.Button(bf, text='모두 저장', command=self.save).pack(side='left', padx=4)
        ttk.Button(bf, text='저장하지 않고 닫기', command=self.close).pack(side='left', padx=4)
        w.protocol('WM_DELETE_WINDOW', self.close)

    def save(self):
        ok = bad = 0
        left = []
        for it, e in self.rows:
            raw = e.get().strip()
            ans = CH.valid_answer(it['kind'], raw) if raw else None
            if ans is None:
                if raw:
                    bad += 1
                    e.configure(foreground='#c0262d')
                left.append((it, e))
                continue
            CH.apply_label(it, ans)
            e.state(['disabled'])
            ok += 1
        self.rows = left
        R.store().reload()
        R._UI_CACHE.clear()
        if bad:
            messagebox.showwarning('Claude 일괄 학습', f'형식이 맞지 않는 칸 {bad}개는 빨간 글씨로 표시했어요. 고치거나 비운 뒤 다시 눌러 주세요.\n'
                                   f'(맞는 {ok}개는 저장했어요)', parent=self.w)
            self.saved = getattr(self, 'saved', 0) + ok
            return
        self.log(f'  ✓ {getattr(self, "saved", 0) + ok}개 학습 완료. 다음 읽기부터 반영돼요.')
        self.close()

    def close(self):
        try:
            self.w.unbind_all('<MouseWheel>')
        except Exception:
            pass
        self.w.destroy()
        if self.on_done:
            self.on_done()


def run_audit(root, log, call, on_done=None, link=None):
    """지금까지 학습된 그림의 정답이 맞는지 Claude가 점검"""
    log('')
    log('■ Claude로 학습 내용 점검')

    def work():
        try:
            lk = link or ClaudeLink(log)
            items, flagged = CH.audit_templates(lk, log)
            if not flagged:
                log(f'  ✓ {len(items)}개 모두 저장된 정답과 Claude가 읽은 값이 같아요.')
                if on_done:
                    call(on_done)
                return
            log(f'  저장된 정답과 다르게 보이는 그림 {len(flagged)}개 → 확인 창에서 골라 주세요.')
            call(lambda: AuditWindow(root, flagged, log, on_done))
        except ClaudeError as e:
            log('⚠ ' + str(e))
            if on_done:
                call(on_done)
        except Exception as e:
            log('⚠ 예상하지 못한 오류: ' + str(e))
            log(traceback.format_exc())
            if on_done:
                call(on_done)
    threading.Thread(target=work, daemon=True).start()


class AuditWindow:
    """저장된 정답과 Claude가 읽은 값이 다른 그림만 보여 주고, 고치기/지우기/그대로 두기를 고름"""

    def __init__(self, root, items, log, on_done=None):
        self.log, self.on_done = log, on_done
        w = self.w = tk.Toplevel(root)
        w.title('Claude 학습 내용 점검')
        w.geometry('700x700')
        ttk.Label(w, text='예전에 저장한 정답과 Claude가 읽은 값이 다른 그림이에요.\n'
                          '각 줄에서 [고치기](입력칸의 값으로) / [지우기] / [그대로 두기] 중 하나를 고르고 [적용]을 누르세요.\n'
                          '(입력칸은 직접 고쳐도 돼요. Claude가 틀렸으면 그대로 두기)', wraplength=660).pack(padx=10, pady=(10, 4), anchor='w')
        outer = ttk.Frame(w)
        outer.pack(fill='both', expand=True, padx=10)
        cv = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient='vertical', command=cv.yview)
        inner = ttk.Frame(cv)
        inner.bind('<Configure>', lambda e: cv.configure(scrollregion=cv.bbox('all')))
        cv.create_window((0, 0), window=inner, anchor='nw')
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        cv.bind_all('<MouseWheel>', lambda e: cv.yview_scroll(int(-e.delta / 120), 'units'))
        self.rows, self.photos = [], []
        for i, it in enumerate(items):
            im = it['img']
            if it['kind'] in ('ovr_digit', 'angle_digit', 'label_char'):
                from PIL import ImageOps
                im = ImageOps.expand(ImageOps.invert(im.convert('L')), border=3, fill=255)
            sc = 1 if it['kind'] == 'card_type' else max(1, min(4, 60 // max(1, im.height)))
            ph = ImageTk.PhotoImage(im.resize((im.width * sc, im.height * sc), Image.NEAREST))
            self.photos.append(ph)
            ttk.Label(inner, image=ph).grid(row=i, column=0, padx=4, pady=4, sticky='w')
            cl = it.get('claude')
            ttk.Label(inner, text=f"{KIND_KO.get(it['kind'], it['kind'])}\n저장된 정답: {it['label']}\n"
                                  f"Claude: {cl if cl else '알아보기 어려움'}",
                      font=('맑은 고딕', 9)).grid(row=i, column=1, padx=6, sticky='w')
            var = tk.StringVar(value='fix' if cl else 'keep')
            e = ttk.Entry(inner, width=6, font=('맑은 고딕', 12), justify='center')
            e.insert(0, cl or it['label'])
            e.grid(row=i, column=2, padx=4)
            f = ttk.Frame(inner)
            f.grid(row=i, column=3, sticky='w')
            if it['kind'] != 'card_type':
                ttk.Radiobutton(f, text='고치기', variable=var, value='fix').pack(anchor='w')
            ttk.Radiobutton(f, text='지우기', variable=var, value='del').pack(anchor='w')
            ttk.Radiobutton(f, text='그대로 두기', variable=var, value='keep').pack(anchor='w')
            self.rows.append((it, e, var))
        bf = ttk.Frame(w)
        bf.pack(pady=8)
        ttk.Button(bf, text='적용', command=self.apply).pack(side='left', padx=4)
        ttk.Button(bf, text='아무것도 바꾸지 않고 닫기', command=self.close).pack(side='left', padx=4)
        w.protocol('WM_DELETE_WINDOW', self.close)

    def apply(self):
        fixed = deleted = 0
        for it, e, var in self.rows:
            act = var.get()
            if act == 'del':
                CH.delete_template(it)
                deleted += 1
            elif act == 'fix':
                ans = CH.valid_answer(it['kind'], e.get().strip())
                if ans is None:
                    messagebox.showwarning('학습 내용 점검', f"'{e.get().strip()}'은(는) {KIND_KO.get(it['kind'])} 형식이 아니에요. 고친 뒤 다시 눌러 주세요.",
                                           parent=self.w)
                    return
                if ans != it['label']:
                    CH.relabel_template(it, ans)
                    fixed += 1
        R.store().reload()
        R._UI_CACHE.clear()
        self.log(f'  ✓ 정답 고침 {fixed}개, 지움 {deleted}개. 다음 읽기부터 반영돼요.')
        self.rows = []
        self.close()

    def close(self):
        try:
            self.w.unbind_all('<MouseWheel>')
        except Exception:
            pass
        self.w.destroy()
        if self.on_done:
            self.on_done()


def main():
    """클로드학습.bat 으로 따로 실행할 때"""
    import gui
    root = tk.Tk()
    try:
        gui.setup_style(root)            # 본 프로그램과 같은 밝은 모양
    except Exception:
        pass
    MONO = gui.MONO
    root.title('Claude 일괄 학습')
    root.geometry('620x360')
    txt = tk.Text(root, font=MONO, height=16, relief='flat', highlightthickness=0, padx=8, pady=6)
    txt.pack(fill='both', expand=True, padx=8, pady=8)
    import queue
    q = queue.Queue()

    def log(m, tag=None):
        q.put(('log', m))

    def call(fn):
        q.put(('call', fn))

    def pump():
        try:
            while True:
                k, v = q.get_nowait()
                if k == 'log':
                    txt.insert('end', v + '\n')
                    txt.see('end')
                else:
                    v()
        except queue.Empty:
            pass
        root.after(150, pump)
    pump()
    n = len(CH.load_pending())
    log(f'학습 대기 {n}개. [시작]을 누르면 Claude Code가 그림을 보고 정답을 붙여요.')
    log('[학습 내용 점검]은 지금까지 저장한 정답이 맞는지 Claude가 다시 확인해요.')
    bf = ttk.Frame(root)
    bf.pack(pady=6)
    btn = ttk.Button(bf, text='시작')

    def start():
        btn.state(['disabled'])
        run_batch_learning(root, log, call, on_done=lambda: btn.state(['!disabled']))
    btn.configure(command=start)
    btn.pack(side='left', padx=4)
    btn2 = ttk.Button(bf, text='학습 내용 점검')

    def start2():
        btn2.state(['disabled'])
        run_audit(root, log, call, on_done=lambda: btn2.state(['!disabled']))
    btn2.configure(command=start2)
    btn2.pack(side='left', padx=4)
    ttk.Button(bf, text='닫기', command=root.destroy).pack(side='left', padx=4)
    root.mainloop()


if __name__ == '__main__':
    main()

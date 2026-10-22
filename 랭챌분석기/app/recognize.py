# -*- coding: utf-8 -*-
"""화면 글자/숫자 인식.

- 흰 바탕 숫자, 스킬 이름, 좌우 표시: Tesseract(글자 인식 프로그램)로 읽음
- 색깔 글자(잠재 등급, 스킬 레벨, 카드 OVR, 발사각): 저장해 둔 샘플과 모양을 비교해서 읽음
- 확신이 낮은 값은 '학습대기' 폴더에 저장해 두고, 나중에 [학습 대기 확인]에서 정답을 알려주면
  샘플로 추가되어 다음부터 정확하게 읽습니다.
"""
import os, re, json, uuid, difflib, subprocess, tempfile, threading, shutil
import numpy as np
import cv2
from PIL import Image, ImageOps

from regions import AUTO, REF_W, REF_H, DETAIL, P1, P2, LOBBY, ORDER, LINEUP, lobby_row, order_row

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
TEMPLATE_DIR = os.path.join(APP_DIR, 'templates')
PENDING_DIR = os.path.join(ROOT_DIR, '학습대기')
TESSDATA_DIR = os.path.join(ROOT_DIR, 'tessdata')

GRADES = ['E', 'D', 'D+', 'C', 'C+', 'B', 'B+', 'A', 'A+', 'S', 'S+', 'SS', 'SS+', 'SR', 'SR+']


# ============================================================ Tesseract
class Tess:
    exe = None

    @classmethod
    def find(cls, configured=None):
        cands = [configured] if configured else []
        cands += [shutil.which('tesseract'),
                  r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                  r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                  os.path.expandvars(r'%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe')]
        for c in cands:
            if c and os.path.isfile(c):
                cls.exe = c
                return c
        return None

    tessdata = None

    @classmethod
    def data_dir(cls):
        """언어 데이터 폴더. 경로에 한글이 있으면 Tesseract가 못 읽을 수 있어서 영어 경로로 복사해서 씀"""
        if cls.tessdata:
            return cls.tessdata
        d = TESSDATA_DIR
        if not d.isascii():
            base = os.environ.get('PROGRAMDATA') or os.environ.get('PUBLIC') or 'C:\\'
            alt = os.path.join(base, 'rankchal_tessdata')
            if not alt.isascii():
                alt = 'C:\\rankchal_tessdata'
            os.makedirs(alt, exist_ok=True)
            for fn in os.listdir(d):
                src, dst = os.path.join(d, fn), os.path.join(alt, fn)
                if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                    shutil.copyfile(src, dst)
            d = alt
        cls.tessdata = d
        return d

    @classmethod
    def run(cls, img, lang='eng', psm=7, whitelist=None):
        if cls.exe is None and cls.find() is None:
            raise RuntimeError('Tesseract를 찾을 수 없어요. 설명서의 "Tesseract 설치" 부분을 확인해 주세요.')
        import io
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        cmd = [cls.exe, 'stdin', 'stdout', '-l', lang, '--psm', str(psm), '--tessdata-dir', cls.data_dir()]
        if whitelist:
            cmd += ['-c', 'tessedit_char_whitelist=' + whitelist]
        flags = 0x08000000 if os.name == 'nt' else 0   # 검은 창 안 뜨게
        out = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, creationflags=flags).stdout
        return out.decode('utf-8', 'ignore').strip()


# ============================================================ 전처리
def _arr(crop):
    return np.asarray(crop.convert('RGB')).astype(np.int32)


def prep(crop, mode, scale=3):
    """글자를 검정, 배경을 흰색으로 만든 확대 이미지"""
    a = _arr(crop).astype(np.float32)
    if mode == 'dark_on_light':
        g = a.min(2)
    else:  # light_on_dark
        g = 255 - a.max(2)
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC).astype(np.uint8)
    _, t = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    t = cv2.copyMakeBorder(t, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(t)


def bright_mask(crop, thr=140, use_sat=False):
    a = _arr(crop)
    v = (a.max(2) - a.min(2)) if use_sat else a.max(2)
    return (v > thr).astype(np.uint8)


def mask_to_img(m, scale=4):
    m = cv2.resize(m * 255, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    m = cv2.copyMakeBorder(255 - m, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(m.astype(np.uint8))


def shape_feature(m, size=(32, 40)):
    ys, xs = np.where(m > 0)
    if len(xs) == 0:
        return None
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    m = cv2.resize(m, size, interpolation=cv2.INTER_AREA)
    m -= m.mean()
    n = np.linalg.norm(m)
    return (m / n).ravel() if n else m.ravel()


def letter_color(crop, m):
    a = _arr(crop)
    px = a[m > 0]
    if len(px) == 0:
        return np.zeros(3)
    c = px.mean(0)
    return c / (np.linalg.norm(c) + 1e-6)


# ============================================================ 샘플(템플릿) 저장소
class TemplateStore:
    """templates/<종류>/<정답>__<번호>.png 형태로 저장된 샘플과 비교"""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}   # kind -> list of (label, feature, color)
        self.reload()

    def _feat(self, kind, img):
        if kind == 'grade':
            m = bright_mask(img, 140)
            f = shape_feature(m, (40, 28))
            return f, letter_color(img, m)
        if kind == 'level':
            m = bright_mask(img, 140)
            return shape_feature(m, (24, 32)), None
        if kind in ('ovr_digit', 'angle_digit', 'label_char'):
            m = np.asarray(img.convert('L')) > 127
            return shape_feature(m.astype(np.uint8), (20, 30)), None
        raise ValueError(kind)

    def reload(self):
        with self.lock:
            self.items = {}
            if not os.path.isdir(TEMPLATE_DIR):
                return
            for kind in os.listdir(TEMPLATE_DIR):
                d = os.path.join(TEMPLATE_DIR, kind)
                if not os.path.isdir(d) or kind == 'ui':
                    continue
                lst = []
                for fn in os.listdir(d):
                    if not fn.endswith('.png'):
                        continue
                    label = fn.split('__')[0].replace('plus', '+')
                    try:
                        f, c = self._feat(kind, Image.open(os.path.join(d, fn)))
                    except Exception:
                        continue
                    if f is not None:
                        lst.append((label, f, c))
                self.items[kind] = lst

    def classify(self, kind, img):
        """(정답 추정, 확신도 0~1)"""
        f, c = self._feat(kind, img)
        if f is None:
            return None, 0.0
        best = (None, -1.0)
        for label, g, gc in self.items.get(kind, []):
            if len(g) != len(f):
                continue
            s = float(f @ g)
            if kind == 'grade' and c is not None and gc is not None:
                s = 0.7 * s + 0.3 * float(c @ gc)     # 글자 색(등급 계열)도 같이 비교
            if s > best[1]:
                best = (label, s)
        return best

    def ranked(self, kind, img):
        """정답 후보별 가장 높은 점수 [(정답, 점수)] 높은 순"""
        f, c = self._feat(kind, img)
        if f is None:
            return []
        best = {}
        for label, g, gc in self.items.get(kind, []):
            if len(g) != len(f):
                continue
            s = float(f @ g)
            if kind == 'grade' and c is not None and gc is not None:
                s = 0.7 * s + 0.3 * float(c @ gc)
            if s > best.get(label, -1.0):
                best[label] = s
        return sorted(best.items(), key=lambda x: -x[1])

    def add(self, kind, label, img):
        d = os.path.join(TEMPLATE_DIR, kind)
        os.makedirs(d, exist_ok=True)
        safe = label.replace('+', 'plus')
        img.save(os.path.join(d, f'{safe}__{uuid.uuid4().hex[:8]}.png'))


STORE = None


def store():
    global STORE
    if STORE is None:
        STORE = TemplateStore()
    return STORE


PENDING_LOG = []      # 이번 실행에서 학습대기에 넣은 것 [(key, kind, context)] — Claude 확인 후 자동 학습에 씀


def save_pending(kind, img, guess, context=''):
    """확신이 낮은 값을 학습대기 폴더에 저장"""
    os.makedirs(PENDING_DIR, exist_ok=True)
    key = uuid.uuid4().hex[:10]
    img.save(os.path.join(PENDING_DIR, key + '.png'))
    with open(os.path.join(PENDING_DIR, key + '.json'), 'w', encoding='utf-8') as f:
        json.dump({'kind': kind, 'guess': guess, 'context': context}, f, ensure_ascii=False)
    PENDING_LOG.append((key, kind, context))
    return key


# ============================================================ 화면 상태 확인
def load_ui(name):
    p = os.path.join(TEMPLATE_DIR, 'ui', name + '.png')
    return Image.open(p).convert('RGB') if os.path.isfile(p) else None


_UI_CACHE = {}


def ui_score(img, name, box, pad=12):
    """기준 이미지와 화면의 같은 위치를 비교 (1에 가까울수록 같음)"""
    if name not in _UI_CACHE:
        _UI_CACHE[name] = load_ui(name)
    t = _UI_CACHE[name]
    if t is None:
        return 0.0
    x1, y1, x2, y2 = box
    area = img.crop((max(0, x1 - pad), max(0, y1 - pad), min(REF_W, x2 + pad), min(REF_H, y2 + pad)))
    a = cv2.cvtColor(np.asarray(area), cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(np.asarray(t), cv2.COLOR_RGB2GRAY)
    if a.shape[0] < b.shape[0] or a.shape[1] < b.shape[1]:
        return 0.0
    r = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
    return float(r.max())


SCREENS = {
    'detail': ('ui_detail', DETAIL['anchor']),
    'lobby': ('ui_lobby', LOBBY['anchor']),
    'order': ('ui_order', ORDER['anchor']),
    'lineup': ('ui_lineup', LINEUP['anchor']),
    'refresh_popup': ('ui_refresh_popup', AUTO['refresh_popup_box']),
    'cont_popup': ('ui_cont_popup', AUTO['cont_popup_box']),
    'save_confirm': ('ui_save_confirm', AUTO['save_confirm_box']),
    'series_result': ('ui_series_result', AUTO['series_result_box']),
    'game_result': ('ui_game_result', AUTO['game_result_box']),
}


def order_panel_open(img):
    """순서 변경 화면에서 투수를 눌렀을 때 아래 '선수 비교' 창이 떠 있는지"""
    return ui_score(img, 'ui_order_panel', AUTO['order_panel_box'], pad=10) >= 0.7


def _colors(img, box):
    a = _arr(img.crop(box)).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    blue = int(((b > 110) & (b > r + 40) & (r < 110)).sum())
    yellow = int(((r > 200) & (g > 150) & (b < 90)).sum())
    return blue, yellow


def refresh_done(img):
    """갱신 버튼이 회색 '금일 갱신 완료' 인지.
    평소 버튼(무료/포인트/스타)은 바탕이 거의 흰색인데, 완료 버튼은 바탕 전체가 어두운 회색이라 밝기로 구분"""
    a = _arr(img.crop(AUTO['refresh_box'])).astype(int).min(axis=2)
    white = float((a > 215).mean())              # 흰 바탕 비율: 평소 0.37~0.67 / 완료 0.00
    gray = float(((a > 160) & (a < 205)).mean())  # 회색 바탕 비율: 평소 0.07~0.08 / 완료 0.66
    return white < 0.15 and gray > 0.35


def refresh_kind(img):
    """로비 갱신 버튼: 'free'(무료 갱신 n/3) / 'point'(P 아이콘) / 'star'(스타 아이콘) / 'done'(금일 갱신 완료)"""
    if refresh_done(img):
        return 'done'
    blue, yellow = _colors(img, AUTO['refresh_icon_box'])
    if yellow > 40:
        return 'star'
    if blue > 60:
        return 'point'
    return 'free'


def refresh_popup_kind(img):
    """즉시 갱신 창의 결제 버튼에 스타 아이콘이 있으면 'star', 아니면 'point' (버튼 자체가 파란색이라 노란색만 봄)"""
    _, yellow = _colors(img, AUTO['refresh_pay_box'])
    return 'star' if yellow > 40 else 'point'


DETAIL_CLOSE_BOX = (420, 1530, 500, 1605)      # 상세 화면 맨 아래 X 버튼
DETAIL_ARROW_BOX = (140, 1442, 205, 1500)      # 상세 화면 아래 « 페이지 화살표


def is_detail(img):
    """선수 상세 화면인지: 모든 카드에 공통으로 있는 아래 X 버튼과 « 화살표로 판단.
    ([특이폼/페이스 미리보기] 버튼은 가명 처리된 선수(예: 호스틴'99) 카드에는 없어서 그것만으로는 못 알아봄)"""
    return min(ui_score(img, 'ui_detail_close', DETAIL_CLOSE_BOX, pad=8),
               ui_score(img, 'ui_detail_arrow', DETAIL_ARROW_BOX, pad=8))


CONT_GO_BOX = (200, 1395, 720, 1445)            # 연속 경기 창 맨 아래 파란 [경기 진행] 버튼


def cont_go_blue(img):
    a = _arr(img.crop(CONT_GO_BOX)).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((b > 180) & (r < 60) & (g > 40) & (g < 140)).mean())


def which_screen(img, thr=0.7):
    best = (None, 0.0)
    for k, (name, box) in SCREENS.items():
        s = ui_score(img, name, box)
        if k == 'detail':
            s = max(s, is_detail(img) - 0.05)          # 특이폼 버튼이 없어도 X·« 가 둘 다 보이면 상세 화면
        if k == 'cont_popup' and s >= 0.45 and cont_go_blue(img) > 0.6:
            # 글자 비교 점수가 조금 낮게 나와도(화면 배율·흐림 차이) 파란 [경기 진행] 버튼이 있으면 연속 경기 창
            s = max(s, 0.8)
        if s > best[1]:
            best = (k, s)
    return best[0] if best[1] >= thr else None


def card_popup_open(img):
    """라인업에서 카드를 눌렀을 때 아래에 [상세 정보] 팝업이 떴는지"""
    return ui_score(img, 'ui_card_popup', LINEUP['popup_box'], pad=10) >= 0.7


def is_ranking_mode(img):
    return ui_score(img, 'ui_mode_rc', LINEUP['mode'], pad=8) >= 0.75


def page_dots(img):
    """화면 아래 페이지 동그라미들을 찾아서 [(가운데 x, 밝기), ...] 로 돌려줌.
    카드 종류에 따라 동그라미가 4개(일반) 또는 5개(이달의 선수 등)라서 위치를 고정하지 않음"""
    a = _arr(img).astype(int)[:, :, :3].sum(2)
    y = DETAIL['dot1'][1]
    x0, x1 = 250, 670
    row = a[y - 6:y + 7, x0:x1].max(0)
    bg = float(np.median(row))
    dots, run = [], []
    for x in list(np.where(row > bg + 80)[0]) + [10 ** 6]:
        if run and x - run[-1] > 2:
            if 8 <= len(run) <= 30:
                dots.append((x0 + (run[0] + run[-1]) // 2, int(row[run].max())))
            run = []
        run.append(x)
    return dots


def page_dots(img):
    """화면 아래 페이지 동그라미들을 찾아 (가운데 x, 밝기) 목록으로 돌려줌. 4개(일반)/5개(이달의 선수 등) 모두 처리"""
    a = _arr(img).astype(int).sum(2)
    y = DETAIL['dot1'][1]
    band = a[y - 8:y + 9, 250:670].max(0)
    on = band > 130                                 # 배경 ~60, 꺼진 동그라미 ~235, 켜진 동그라미 ~760
    dots, x = [], 0
    while x < len(on):
        if on[x]:
            x0 = x
            while x < len(on) and on[x]:
                x += 1
            if 5 <= x - x0 <= 30:
                dots.append((250 + (x0 + x) // 2, int(band[x0:x].max())))
        x += 1
    return dots


def detail_page(img):
    """상세 화면이 1페이지(스탯)인지 2페이지(잠재/스킬)인지.
    1페이지 왼쪽 스탯 이름('파워'/'구속')이 보이는지로 판단 (카드마다 페이지 동그라미 개수가 달라서 동그라미는 보조로만)"""
    lab = max(ui_score(img, 'label_batter', P1['label1'], pad=6), ui_score(img, 'label_pitcher', P1['label1'], pad=6))
    if lab >= 0.5:
        return 1
    if lab < 0.35:
        return 2
    return detail_page_dots(img)


def detail_page_dots(img):
    dots = page_dots(img)
    if len(dots) >= 2:
        k = max(range(len(dots)), key=lambda i: dots[i][1])
        return k + 1
    a = _arr(img)                                   # 동그라미를 못 찾으면 예전 방식
    v1 = a[DETAIL['dot1'][1], DETAIL['dot1'][0]].sum()
    v2 = a[DETAIL['dot2'][1], DETAIL['dot2'][0]].sum()
    return 1 if v1 > v2 else 2


def is_pitcher_page1(img):
    sb = ui_score(img, 'label_batter', P1['label1'], pad=6)
    sp = ui_score(img, 'label_pitcher', P1['label1'], pad=6)
    return sp > sb


def tab_selected(img, which):
    """라인업 화면에서 타자/투수 탭 중 어느 쪽이 선택됐는지 (선택된 탭은 파란 배경)"""
    a = _arr(img.crop(LINEUP['tab_batter_box' if which == 'batter' else 'tab_pitcher_box']))
    return a[..., 2].mean() - a[..., 0].mean() > 60


# ============================================================ 개별 값 읽기
def read_white_numbers(img, box):
    """흰 바탕의 숫자 3개 (세로 열)"""
    s = Tess.run(prep(img.crop(box), 'dark_on_light'), 'eng', 6, '0123456789')
    nums = [int(x) for x in re.findall(r'\d+', s)]
    return nums


def read_number_tess(crop, mode='dark_on_light', psm=7):
    s = Tess.run(prep(crop, mode), 'eng', psm, '0123456789')
    return int(s) if s.isdigit() else None


def _segment_digits(m):
    """숫자 마스크를 글자 하나씩 잘라냄 (붙어 있는 두 글자는 가운데 가장 얇은 곳에서 나눔)"""
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return []
    hmax = max(st[i, 3] for i in range(1, n))
    comps = [(st[i, 0], i) for i in range(1, n)
             if st[i, 3] > hmax * 0.7 and st[i, 4] > 25 and st[i, 2] >= st[i, 3] * 0.12]
    comps.sort()
    pieces = []
    for x, i in comps:
        g = (lab == i).astype(np.uint8)
        ys, xs = np.where(g)
        g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = g.shape
        if w > h * 1.05:                       # 두 글자가 붙은 경우
            col = g.sum(0).astype(float)
            lo, hi = int(w * 0.3), int(w * 0.7)
            cut = lo + int(np.argmin(col[lo:hi]))
            pieces += [g[:, :cut], g[:, cut:]]
        else:
            pieces.append(g)
    glyphs = []
    for g in pieces:
        ys, xs = np.where(g)
        if len(xs) == 0:
            continue
        g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        glyphs.append(Image.fromarray(g * 255))
    return glyphs


def read_colored_number(img, box, kind, mask_fn, lo, hi, ctx):
    """색깔 숫자: 글자 하나씩 샘플과 비교 -> 안 되면 Tesseract -> 그래도 이상하면 학습대기"""
    crop = img.crop(box)
    m = mask_fn(crop)
    glyphs = _segment_digits(m)
    st = store()
    digits, confs = [], []
    for g in glyphs:
        lab, c = st.classify(kind, g)
        digits.append(lab)
        confs.append(c)
    val = None
    if digits and all(d is not None for d in digits) and min(confs) >= 0.80:
        try:
            val = int(''.join(digits))
        except ValueError:
            val = None
    if val is None or not (lo <= val <= hi):
        s = Tess.run(mask_to_img(m), 'eng', 7, '0123456789')
        val2 = int(s) if s.isdigit() else None
        if val2 is not None and lo <= val2 <= hi:
            val = val2
        else:
            val = None
    ok = val is not None and (digits and min(confs) >= 0.80)
    if not ok:
        for gi, (g, d, c) in enumerate(zip(glyphs, digits, confs)):
            if c < 0.80:
                save_pending(kind, g, d or '', f'{ctx} #{gi}/{len(glyphs)}')
    return val, ok


def ovr_mask(crop):
    """카드 OVR 숫자: 영역에서 가장 진한 색(숫자 색)과 비슷한 픽셀만 남김"""
    a = _arr(crop)
    mx = a.max(2)
    sat = mx - a.min(2)
    score = sat + mx
    ref = a[score >= np.percentile(score, 85)].mean(0)
    dist = np.sqrt(((a - ref) ** 2).sum(2))
    m = ((dist < 70) & (sat > 70)).astype(np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def angle_mask(crop):
    return bright_mask(crop, 170)


# 잠재 등급 글자 색(색상각, 0~360도)
_GRADE_HUE = {'A': 353, 'B': 143, 'C': 201, 'D': 275, 'S': 53, 'SR': 33}


def grade_by_rule(crop):
    """글자 색으로 계열(A/B/C/D/S/SR/E)을, 글자 폭과 '+' 모양으로 세부 등급을 판정"""
    a = np.asarray(crop.convert('RGB'))
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    col = (hsv[..., 1] > 80) & (hsv[..., 2] > 120)
    if col.sum() < 15:
        return '-' if hsv[..., 2].astype(float).std() < 20 else 'E'
    h = hsv[..., 0][col].astype(float) * 2 * np.pi / 180
    ang = np.degrees(np.arctan2(np.sin(h).mean(), np.cos(h).mean())) % 360
    fam = min(_GRADE_HUE, key=lambda k: min(abs(ang - _GRADE_HUE[k]), 360 - abs(ang - _GRADE_HUE[k])))
    ys, xs = np.where(col)
    sub = col[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    hh, w = sub.shape
    top = sub[:int(hh * 0.2), int(w * 0.62):].mean()   # 오른쪽 윗부분: '+'면 비어 있음
    if fam in ('A', 'B', 'C', 'D'):
        return fam + ('+' if w > 24 else '')
    if fam == 'SR':
        return 'SR' + ('+' if top < 0.5 else '')
    if w < 24:
        return 'S'
    if top < 0.1:
        return 'S+'
    if top < 0.5:
        return 'SS+'
    return 'SS'


def read_grade(img, box, ctx):
    """잠재 등급: 글자 색 + 모양 규칙으로 판정 (샘플 없이도 정확)"""
    return grade_by_rule(img.crop(box)), True


def read_level(img, box, ctx):
    """스킬 레벨(5~10): 샘플 비교 + Tesseract. 둘이 다를 때만 학습대기에 저장 (경고는 띄우지 않음)"""
    crop = img.crop(box)
    lab, c = store().classify('level', crop)
    try:
        val = int(lab)
    except (TypeError, ValueError):
        val = None
    if val is not None and c >= 0.9:
        return val, True
    s = Tess.run(mask_to_img(bright_mask(crop, 140)), 'eng', 7, '0123456789')
    t = int(s) if s.isdigit() and 1 <= int(s) <= 10 else None
    if t is not None and val is not None and t != val:
        save_pending('level', crop, str(t), ctx)
    elif val is None and t is None:
        save_pending('level', crop, '', ctx)
    pick = val if (val is not None and c >= 0.75) else (t if t is not None else val)
    return pick, True


def read_stars(img):
    """별 개수: 5성은 분홍 별, 4성 이하는 노란 별"""
    a = np.asarray(img.crop(P1['stars']).convert('RGB'))
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV).astype(int)
    h, s, v = hsv[..., 0] * 2, hsv[..., 1], hsv[..., 2]
    magenta = ((h > 290) & (h < 340) & (s > 90) & (v > 150)).sum()
    if magenta > 300:
        return 5
    yellow = ((h > 40) & (h < 70) & (s > 90) & (v > 200)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(yellow)
    cnt = sum(1 for i in range(1, n) if st[i, 4] > 25)
    return max(1, min(4, cnt)) if cnt else 4


def _side_of(ch):
    """글자의 첫 자음으로 좌/우/양 판별 (ㅈ->좌, ㅇ->우 또는 양, ㅁ->우(잘못 읽힘))"""
    if not ch or not ('가' <= ch <= '힣'):
        return None
    code = ord(ch) - 0xAC00
    ini, med, jong = code // 588, (code % 588) // 28, code % 28
    if ini == 12:                       # ㅈ
        return 'L'
    if ini == 11:                       # ㅇ: '우'는 받침 없음+ㅜ, '양'은 받침 ㅇ (엉/앙/영으로 잘못 읽혀도 받침이 있으면 양)
        return 'S' if (jong == 21 or med in (0, 2, 4, 6)) else 'R'
    if ini == 6:                        # ㅁ (우를 무로 읽는 경우)
        return 'R'
    return None


def read_hand(img):
    """'우투좌타' -> ('R','L'). '/' 뒤 4글자 중 1번째(투), 3번째(타) 글자의 첫 자음으로 판별"""
    crop = img.crop(P2['hand'])
    for scale in (3, 4, 5):
        s = Tess.run(prep(crop, 'light_on_dark', scale), 'kor', 7).replace(' ', '')
        tail = s.split('/')[-1]
        tail = re.sub(r'[^가-힣]', '', tail)
        if len(tail) >= 4:
            t, b = _side_of(tail[-4]), _side_of(tail[-2])
            if t in ('L', 'R') and b:
                return t, b, True
    return None, None, False


CARD_TYPES = ['시그', '골글', '임팩', '국대', '올스타', '라이브']


def read_card_type(img):
    """2페이지 카드 아래 워터마크(Signature / Golden Glove / IMPACT ...)를 저장된 이미지와 비교.
    처음 보는 종류는 '시그'로 보고 학습대기에 저장합니다."""
    best = ('시그', 0.0)
    for t in CARD_TYPES:
        s = ui_score(img, 'wm_' + t, P2['watermark'], pad=10)
        if s > best[1]:
            best = (t, s)
    if best[1] < 0.6:
        # 추측이 약하면 비워 둠 (점수 계산은 시그 기준으로 처리).
        # 카드 종류는 지금 점수에 거의 영향이 없어서 학습대기/Claude 확인 대상에서 뺐어요 (2026-10-18).
        return best[0] if best[1] >= 0.45 else None
    return best[0]


BANNER_TYPES = [('올스타', '올스타'), ('국가대표', '국대'), ('시그니처', '시그'), ('골든글러브', '골글'),
                ('골든', '골글'), ('임팩트', '임팩'), ('라이브', '라이브')]
BANNER_BOX = (263, 48, 655, 97)          # 상세 화면 맨 위 배너 ("이달의 선수", "올스타 카드" 등)


def read_banner_type(img):
    """상세 화면 위쪽 배너 글자로 카드 종류 판단. 배너가 없거나 모르는 글자면 None"""
    t = Tess.run(prep(img.crop(BANNER_BOX), 'light_on_dark'), 'kor', 7).replace(' ', '')
    for key, ct in BANNER_TYPES:
        if key in t:
            return ct
    return None


# ============================================================ 스킬 이름
class SkillNames:
    def __init__(self, names_batter, names_pitcher):
        self.b = sorted(set(names_batter))
        self.p = sorted(set(names_pitcher))

    def match(self, text, pitcher):
        t = re.sub(r'[^가-힣0-9A-Za-z]', '', text or '')
        if not t:
            return None, 0.0
        pool = self.p if pitcher else self.b
        best = max(((difflib.SequenceMatcher(None, t, n).ratio(), n) for n in pool), default=(0, None))
        return best[1], best[0]


def read_skill_name(img, box):
    s = Tess.run(prep(img.crop(box), 'light_on_dark'), 'kor', 7)
    return s.replace(' ', '')


POSITIONS = ['C', '1B', '2B', '3B', 'SS', 'LF', 'CF', 'RF', 'DH', 'SP', 'RP', 'CP']


def read_pos(img):
    x1, y1, x2, y2 = P1['pos']
    crop = img.crop((x1 + 5, y1 + 5, x2 - 5, y2 - 5))      # 테두리 제외
    s = ''
    for thr in (150, 100):                                   # 하늘색 글자, 회색 글자
        s = Tess.run(mask_to_img(bright_mask(crop, thr), 4), 'eng', 7, 'CDHSPRLFB123').replace(' ', '').upper()
        if s in POSITIONS:
            return s
    best = difflib.get_close_matches(s, POSITIONS, n=1, cutoff=0.4)
    return best[0] if best else s


NAME_FIX_FILE = os.path.join(APP_DIR, 'data', 'name_fix.json')
_NAME_FIX = None


def name_fixes():
    global _NAME_FIX
    if _NAME_FIX is None:
        try:
            _NAME_FIX = json.load(open(NAME_FIX_FILE, encoding='utf-8'))
        except (OSError, ValueError):
            _NAME_FIX = {}
    return _NAME_FIX


def add_name_fix(wrong, right):
    """Claude가 고친 이름을 기억해 두고 다음부터 자동으로 고침"""
    if not wrong or not right or wrong == right:
        return
    f = name_fixes()
    f[wrong] = right
    try:
        with open(NAME_FIX_FILE, 'w', encoding='utf-8') as fh:
            json.dump(f, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def clean_name(text):
    """'로하스B '20' -> '로하스'. 앞쪽 한글 부분만 남김 (연도, 카드 구분 글자 B/S 등 제외). 자주 틀리는 이름은 고침표로 바로잡음"""
    m = re.search(r'[가-힣]{2,}', text or '')
    n = m.group(0) if m else ''
    return name_fixes().get(n, n)


def read_name(img, box):
    crop = img.crop(box)
    best = ''
    for lang, scale in (('kor', 3), ('kor', 4), ('kor+eng', 3)):
        n = clean_name(Tess.run(prep(crop, 'light_on_dark', scale), lang, 7))
        if len(n) > len(best):
            best = n
        if len(n) >= 2:
            break
    return best


# ============================================================ 페이지 단위 읽기
def _pick_stat(cands):
    """같은 칸을 여러 방법으로 읽은 후보 중 고르기. 앞의 '1'만 빠진 후보(63 vs 163)는 세 자리 쪽을 택함"""
    c = [v for v in cands if isinstance(v, int) and 10 <= v <= 250]
    if not c:
        return None
    threes = [v for v in c if v >= 100]
    for t in threes:
        if any(v == t - 100 for v in c):
            return t
    return max(set(c), key=c.count)


BAR_A, BAR_B = 100.0, 0.547     # 스탯 = 100 + 0.547 x 분홍 막대 길이 (84개 스탯으로 확인, 오차 0.5 이내)
# 오른쪽 열(인내/주루/수비 · 제구/지구력/수비) 막대는 평균 0.3 정도 짧게 재져서, 글자를 못 읽고 막대만 쓸 때
# 1 낮게 나오는 일이 많았음 (Claude 진단 메모: 인내 132→133, 주루 111→112 같은 수정이 대부분 오른쪽 열). 그만큼 보정.
BAR_RIGHT_ADJ = 0.3


def bar_value(img, box, span=230):
    """숫자 칸 왼쪽의 막대에서 분홍색 길이를 재서 스탯 추정. 100 미만이면 None"""
    a = np.asarray(img).astype(np.int32)
    x1, y1, x2, y2 = box
    y = (y1 + y2) // 2
    best = 0
    for yy in (y - 2, y, y + 2):
        row = a[yy, max(0, x1 - span):x1]
        pink = (row[:, 0] > 200) & (row[:, 1] < 130) & (row[:, 2] > 120)
        best = max(best, int(pink.sum()))
    if best <= 3:
        return None
    return BAR_A + BAR_B * best


def read_stats6(img, careful=False):
    """1페이지 스탯 6개. 막대 길이로 값을 정하고, 글자 인식 값이 막대와 1 이내로 맞으면 그 값을 씀.
    막대가 비어 있으면(100 미만) 글자 인식 값만 사용"""
    left = read_white_numbers(img, P1['col_left'])
    right = read_white_numbers(img, P1['col_right'])
    out = []
    for i, k in enumerate(('s1', 's2', 's3', 's4', 's5', 's6')):
        col = left if i < 3 else right
        ocr = col[i % 3] if len(col) == 3 else None
        est = bar_value(img, P1[k])
        if est is not None and i >= 3:
            est += BAR_RIGHT_ADJ
        if est is None:                                    # 100 미만
            if ocr is None or ocr >= 100:
                s = Tess.run(prep(img.crop(P1[k]), 'dark_on_light', 4), 'eng', 7, '0123456789')
                ocr = int(s) if s.isdigit() else ocr
            if ocr is not None and ocr >= 100:
                ocr = ocr % 100
            out.append(ocr)
        elif ocr is not None and abs(ocr - est) <= 1.5:
            out.append(ocr)
        else:
            out.append(int(round(est)))
    return out


def _ovr_gap_limit():
    try:
        import score as SC
        return float(SC.SET.get('검산_경고_차이', 1))
    except Exception:
        return 1.0


def fix_with_ovr(stats, ovr_seen):
    """카드 OVR = 6개 스탯 평균(버림). 안 맞으면 '1'이 빠진 칸(100 미만)에 100을 더해서 맞는지 확인"""
    if any(v is None for v in stats):
        return stats, False
    calc = sum(stats) // 6
    if ovr_seen is None or calc == ovr_seen:
        return stats, True
    for n in (1, 2):                                       # 한두 칸에서 '1'을 놓친 경우
        import itertools
        for idx in itertools.combinations([i for i, v in enumerate(stats) if v < 100], n):
            t = [v + 100 if i in idx else v for i, v in enumerate(stats)]
            if sum(t) // 6 == ovr_seen:
                return t, True
    return stats, False


def read_page1(img, ctx=''):
    pitcher = is_pitcher_page1(img)
    name = read_name(img, P1['name'])
    pos = read_pos(img)
    # 카드 OVR 숫자는 검산용으로만 읽음 (학습대기에 저장하지 않음)
    seen, sure = _read_ovr_quiet(img, with_conf=True)
    stats = read_stats6(img)
    stats, ok = fix_with_ovr(stats, seen)
    calc = sum(stats) // 6 if all(v is not None for v in stats) else None
    # 카드 OVR = 6개 스탯 평균(버림)이 정확히 맞음 (기록 362명 중 93% 일치, 97.5%가 ±1 안).
    # 그래서 스탯 평균(calc)을 기본으로 쓰고, 카드 OVR 숫자를 '확실하게' 읽었는데 평균과 다르면
    #   -> 스탯 중 하나를 잘못 읽었을 가능성이 크므로 카드 OVR을 쓰고 '스탯검산' 확인 표시를 붙임.
    # 카드 OVR 숫자를 흐릿하게 읽었으면(템플릿 확신 낮음) 그 숫자는 믿지 않음.
    gap_limit = _ovr_gap_limit()
    bad = (sure and seen is not None and calc is not None and abs(seen - calc) >= gap_limit)
    if calc is None:
        ovr = seen
    elif bad:
        ovr = seen
    else:
        ovr = calc
    d = {'name': name, 'pos': pos, 'pitcher': pitcher, 'ovr': ovr, 'ovr_seen': seen, 'ovr_calc': calc,
         'card_banner': read_banner_type(img),
         'stars': read_stars(img), 'warn': []}
    if bad:
        d['warn'].append(f'스탯검산(카드OVR {seen} / 스탯평균 {calc})')
    left, right = stats[:3], stats[3:]
    if pitcher:
        d.update({'구속': left[0], '변화': left[1], '구위': left[2],
                  '제구': right[0], '지구력': right[1], '수비': right[2]})
    else:
        d.update({'파워': left[0], '정확': left[1], '선구': left[2],
                  '인내': right[0], '주루': right[1], '수비': right[2]})
        ang, ok2 = read_colored_number(img, P1['angle'], 'angle_digit', angle_mask, 0, 40, ctx + ' 발사각')
        d['발사각'] = ang
        if not ok2:
            d['warn'].append('발사각')
    for k, v in list(d.items()):
        if v is None and k not in ('warn', 'ovr_seen', 'ovr_calc', 'card_banner'):
            d['warn'].append(k)
    return d


def _read_ovr_quiet(img, with_conf=False):
    """카드 왼쪽 위 OVR 숫자. with_conf=True면 (값, 확실한지) - 저장된 숫자 그림과 모두 0.8 이상 맞을 때만 확실"""
    crop = img.crop(P1['ovr'])
    m = ovr_mask(crop)
    st = store()
    glyphs = _segment_digits(m)
    digits = [st.classify('ovr_digit', g) for g in glyphs]
    if digits and all(lab is not None and c >= 0.8 for lab, c in digits):
        try:
            v = int(''.join(lab for lab, _ in digits))
            if 50 <= v <= 170:
                return (v, True) if with_conf else v
        except ValueError:
            pass
    s = Tess.run(mask_to_img(m), 'eng', 7, '0123456789')
    v = int(s) if s.isdigit() and 50 <= int(s) <= 170 else None
    return (v, False) if with_conf else v


def read_page2(img, skillnames, pitcher, ctx=''):
    d = {'warn': [], 'name2': read_name(img, P2['title'])}
    grades = []
    for k in ('g1', 'g2', 'g3', 'g4', 'g5', 'g6'):
        g, ok = read_grade(img, P2[k], ctx + ' ' + k)
        grades.append(g)
        if not ok:
            d['warn'].append('잠재' + k[1])
    d['grades'] = grades
    skills = []
    for j, (kb, lb) in enumerate((('k1', 'l1'), ('k2', 'l2'), ('k3', 'l3'))):
        raw = read_skill_name(img, P2[kb])
        nm, sc = skillnames.match(raw, pitcher)
        lv, ok = read_level(img, P2[lb], ctx + f' 레벨{j + 1}')
        if sc < 0.6:
            d['warn'].append('스킬:' + raw)
            nm = raw
        if not ok:
            d['warn'].append('레벨')
        skills.append((nm, lv))
    d['skills'] = skills
    t, b, ok = read_hand(img)
    d['throw'], d['bat'] = t, b
    if not ok:
        d['warn'].append('좌우')
    d['card_type'] = read_card_type(img)
    return d


def label_chars(crop):
    """'1선발 L' 같은 흰 글자 라벨 -> (첫 글자 이미지, 마지막 글자 이미지)"""
    a = np.asarray(crop.convert('RGB'))
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    m = ((hsv[..., 2] > 190) & (hsv[..., 1] < 90)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    comps = [(st[i, 0], i) for i in range(1, n) if st[i, 4] > 6 and st[i, 3] >= m.shape[0] * 0.35]
    if not comps:
        return None, None
    comps.sort()
    def img_of(i):
        g = (lab == i).astype(np.uint8)
        ys, xs = np.where(g)
        return Image.fromarray(g[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255)
    return img_of(comps[0][1]), img_of(comps[-1][1])


def read_label(crop, want_hand=True):
    num, hand, _ = read_label_c(crop, want_hand)
    return num, hand


def read_label_c(crop, want_hand=True):
    """(번호, 손, 확신도). 확신도는 같은 번호가 두 줄에 나왔을 때 어느 쪽이 틀렸는지 고르는 데 씀"""
    first, last = label_chars(crop)
    st = store()
    num = hand = None
    conf = 0.0
    if first is not None:
        rk = [x for x in st.ranked('label_char', first) if x[0] in ('1', '2', '3', '4', '5')]
        lab, c = rk[0] if rk else (None, 0.0)
        gap = c - (rk[1][1] if len(rk) > 1 else 0.0)
        if lab and (c >= 0.8 or (c >= 0.65 and gap >= 0.06)):   # 확실하거나, 2등과 차이가 충분하면 채택
            num = int(lab)
            conf = c
        else:
            g = ImageOps.invert(first.convert('L'))                 # 숫자 한 글자만 Tesseract로 다시
            g = ImageOps.expand(g, border=8, fill=255)
            g = g.resize((g.width * 4, g.height * 4), Image.LANCZOS)
            t = Tess.run(g, 'eng', 10, '12345')
            top2 = [x[0] for x in rk[:2]]
            if t in ('1', '2', '3', '4', '5') and (lab is None or t in top2 or c < 0.45):   # 글자 인식이 모양 비교 1·2등과 맞을 때만
                num = int(t)
                conf = 0.3
            save_pending('label_char', first, str(num) if num else (lab or ''), '선발 번호 라벨')
    if want_hand and last is not None:
        lab, c = st.classify('label_char', last)
        if lab in ('L', 'R', 'S') and c >= 0.8:
            hand = lab
    return num, hand, conf


def fill_rotation(nums, confs=None):
    """내 선발 번호는 다섯 줄에 1~5가 한 번씩 들어감.
    - 한 줄만 못 읽었으면 남은 번호로 채움
    - 같은 번호가 두 줄에 나오고 한 번호가 빠졌으면, 확신도가 낮은 쪽을 빠진 번호로 고침 (예: 5를 4로 잘못 읽음)"""
    nums = list(nums)
    if None not in nums and confs:
        dup = [v for v in set(nums) if nums.count(v) == 2]
        miss = sorted({1, 2, 3, 4, 5} - set(nums))
        if len(dup) == 1 and len(miss) == 1 and len(nums) == 5:
            i, j = [k for k, v in enumerate(nums) if v == dup[0]]
            k = i if (confs[i] or 0) < (confs[j] or 0) else j
            nums[k] = miss[0]
            return nums, True
    known = [n for n in nums if n is not None]
    if nums.count(None) == 1 and len(set(known)) == 4 and set(known) <= {1, 2, 3, 4, 5}:
        miss = ({1, 2, 3, 4, 5} - set(known)).pop()
        return [miss if n is None else n for n in nums], True
    return nums, False


# ============================================================ 로비 / 순서변경 화면
def read_lobby(img):
    rows = []
    for i in range(5):
        r = lobby_row(i)
        nick = Tess.run(prep(img.crop(r['nick']), 'light_on_dark'), 'kor+eng', 7)
        point = read_number_tess(img.crop(r['point']), 'light_on_dark')
        # OVR은 소수점 있음
        s = Tess.run(prep(img.crop(r['ovr']), 'light_on_dark'), 'eng', 7, '0123456789.')
        try:
            ovr = float(s)
        except ValueError:
            ovr = None
        my_sp, _, my_c = read_label_c(img.crop(r['my_label']), want_hand=False)
        res = _read_result(img, r)
        rows.append({'nick': nick.strip(), 'point': point, 'ovr': ovr, 'my_sp': my_sp, 'my_conf': my_c, **res})
    nums, _ = fill_rotation([r['my_sp'] for r in rows], [r.get('my_conf') for r in rows])
    for r, n in zip(rows, nums):
        r['my_sp'] = n
    return rows


def _read_result(img, r):
    pb = Tess.run(prep(img.crop(r['playball']), 'dark_on_light'), 'eng', 7).upper().replace(' ', '')
    if 'PLAY' in pb or 'BALL' in pb:
        return {'result': None, 'score': None}
    a = _arr(img.crop(r['result']))
    rr, gg, bb = a[..., 0], a[..., 1], a[..., 2]
    cyan = ((bb > 150) & (gg > 150) & (rr < 120)).sum()
    red = ((rr > 170) & (gg < 90) & (bb < 110)).sum()
    if cyan > 40 and cyan > red:
        res = '승'
    elif red > 40:
        res = '패'
    else:
        txt = Tess.run(prep(img.crop(r['result']), 'light_on_dark'), 'kor', 7)
        res = '무' if '무' in txt else None
    s = Tess.run(prep(img.crop(r['score']), 'light_on_dark'), 'eng', 7, '0123456789:')
    nums = re.findall(r'\d+', s)
    score = f'{nums[0]}:{nums[1]}' if len(nums) >= 2 else None
    return {'result': res, 'score': score}


def read_order_labels(img):
    """순서변경 화면: 각 줄의 내 선발 번호/손, 상대 선발 번호/손, 상대 타자 OVR"""
    rows = []
    for i in range(5):
        r = order_row(i)
        lab = lambda box: read_label(img.crop(box))
        my_n, my_h, my_c = read_label_c(img.crop(r['my_label']))
        op_n, op_h = lab(r['opp_label'])
        s = Tess.run(prep(img.crop(r['opp_bat_ovr']), 'light_on_dark'), 'eng', 7, '0123456789.')
        try:
            bovr = float(s)
        except ValueError:
            bovr = None
        nick = Tess.run(prep(img.crop(r['nick']), 'light_on_dark'), 'kor+eng', 7).strip()
        rows.append({'my_sp': my_n, 'my_hand': my_h, 'my_conf': my_c, 'opp_sp': op_n, 'opp_hand': op_h,
                     'opp_bat_ovr': bovr, 'nick': nick})
    nums, _ = fill_rotation([r['my_sp'] for r in rows], [r.get('my_conf') for r in rows])
    for r, n in zip(rows, nums):
        r['my_sp'] = n
    return rows

"""ขั้น clean ข้อมูล accelerometer ข้อมือ (ขั้น 2–11)

ทุกฟังก์ชันในไฟล์นี้ไม่รู้ว่าข้อมูลมาจาก dataset ไหน และใช้เกณฑ์จาก config ชุดเดียวกัน
(ข้อยกเว้นเดียวคือ harmonize ซึ่ง config ระบุเองว่าทำกับ dataset ไหน)
ลำดับการเรียกใช้อยู่ใน src/clean/run.py -> clean_subject()

รูปแบบข้อมูลกลางหลังขั้น 4: xyz = ndarray (n, 3) float32 หน่วย g บน grid เวลาสม่ำเสมอ
index 0 = 00:00 ของวันแรก, ความยาวเป็นจำนวนเต็มชั่วโมง, NaN = ไม่มีข้อมูล
ฟังก์ชันที่ชื่อลงท้าย _mask คืน array bool ยาว n (True = ตัวอย่างนั้นติดธง)
"""
import warnings
from math import ceil, gcd

import numpy as np
import pandas as pd
from scipy.signal import resample_poly

# ธงที่ทำให้ตัวอย่างถูกตัด แต่ละธงเป็น 1 bit ใน uint16 (1 ตัวอย่างติดได้หลายธงพร้อมกัน)
# เช่น ตัวอย่างที่ทั้งนอนและนิ่ง = BIT["sleep"] | BIT["ism"]
FLAGS = ("missing", "clip", "abnormal", "ism", "nonwear", "sleep", "visit", "short_day", "few_days")
BIT = {f: np.uint16(1 << i) for i, f in enumerate(FLAGS)}


# ---------------------------------------------------------------- helpers

def runs(mask):
    """หาช่วงที่ mask เป็น True ต่อเนื่อง คืน (starts, ends) โดย end ไม่รวม

    ตัวอย่าง: [F, T, T, F, T] -> starts = [1, 4], ends = [3, 5]
    """
    # เติม False หัวท้ายแล้วหาจุดที่ค่าเปลี่ยน: จุดเปลี่ยนคู่ (0, 2, ...) = เริ่มช่วง, คี่ = จบช่วง
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.flatnonzero(m[1:] != m[:-1])
    return d[::2], d[1::2]


def fill_runs(n, starts, ends):
    """สร้าง mask ยาว n ที่เป็น True ในช่วง [starts[i], ends[i]) (กลับด้านของ runs)

    ช่วงที่ทับกันหรือเลยขอบได้ (ตัดให้อยู่ใน 0..n)
    """
    # +1 ที่จุดเริ่ม, -1 ที่จุดจบ แล้วผลรวมสะสม > 0 = อยู่ในช่วงใดช่วงหนึ่ง
    delta = np.zeros(n + 1, np.int32)
    np.add.at(delta, np.clip(starts, 0, n), 1)
    np.add.at(delta, np.clip(ends, 0, n), -1)
    return np.cumsum(delta[:-1]) > 0


def keep_long_runs(mask, min_len):
    """เก็บเฉพาะช่วง True ที่ยาว >= min_len ตัวอย่าง ช่วงที่สั้นกว่าเปลี่ยนเป็น False

    ใช้กับกฎแบบ "เป็นแบบนี้ต่อเนื่องนานเกิน X วินาที" เช่น ค่าค้าง, ค่า 0
    """
    s, e = runs(mask)
    sel = (e - s) >= min_len
    return fill_runs(len(mask), s[sel], e[sel])


def window_stats(xyz, win, block_samples=4_320_000):
    """สถิติต่อหน้าต่างไม่ทับกันยาว win ตัวอย่าง แยกต่อแกน (ข้ามค่า NaN)

    คืน dict ของ array:
        n      (nwin,)    จำนวนตัวอย่างที่มีค่าในหน้าต่าง
        sum    (nwin, 3)  ผลรวมต่อแกน
        sumsq  (nwin, 3)  ผลรวมกำลังสองต่อแกน (ใช้คำนวณ SD และรวมหน้าต่างต่อกันได้)
        min    (nwin, 3)  ค่าต่ำสุดต่อแกน (NaN ถ้าหน้าต่างไม่มีข้อมูล)
        max    (nwin, 3)  ค่าสูงสุดต่อแกน
    ตัวอย่างท้ายที่ไม่ครบหน้าต่างถูกทิ้ง ทำทีละก้อน (~1 วันที่ 50 Hz) เพื่อไม่ให้สร้าง array ใหญ่ทั้งก้อน
    """
    nwin = len(xyz) // win
    n = np.zeros(nwin, np.int64)
    s = np.zeros((nwin, 3))
    ss = np.zeros((nwin, 3))
    mn = np.full((nwin, 3), np.nan)
    mx = np.full((nwin, 3), np.nan)
    step = max(1, block_samples // win)   # จำนวนหน้าต่างต่อก้อน
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # หน้าต่างที่ไม่มีข้อมูลเลย
        for a in range(0, nwin, step):
            b = min(nwin, a + step)
            w = xyz[a * win:b * win].reshape(b - a, win, 3)   # (หน้าต่าง, ตัวอย่าง, แกน) ไม่ copy
            ok = ~np.isnan(w[..., 0])
            n[a:b] = ok.sum(1)
            w64 = np.where(ok[..., None], w, 0).astype(np.float64)   # NaN -> 0 และใช้ float64 กันผลรวมคลาดเคลื่อน
            s[a:b] = w64.sum(1)
            ss[a:b] = (w64 ** 2).sum(1)
            mn[a:b] = np.nanmin(w, 1)
            mx[a:b] = np.nanmax(w, 1)
    return {"n": n, "sum": s, "sumsq": ss, "min": mn, "max": mx}


def stats_mean(st):
    """ค่าเฉลี่ยต่อหน้าต่างต่อแกน จากผลของ window_stats (NaN ถ้าหน้าต่างไม่มีข้อมูล)"""
    with np.errstate(invalid="ignore", divide="ignore"):
        return st["sum"] / st["n"][:, None]


def stats_sd(st):
    """SD ต่อหน้าต่างต่อแกน (ddof=1) จากผลของ window_stats

    ใช้สูตร var = (Σx² - (Σx)²/n) / (n - 1) ค่าติดลบเล็กน้อยจากการปัดเศษถูกปัดเป็น 0
    """
    n = st["n"][:, None].astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = (st["sumsq"] - st["sum"] ** 2 / n) / (n - 1)
    return np.sqrt(np.clip(var, 0, None))


def per_window(mask, win):
    """mask ระดับตัวอย่าง -> ระดับหน้าต่าง (True ถ้าทุกตัวอย่างในหน้าต่างเป็น True)"""
    nwin = len(mask) // win
    return mask[:nwin * win].reshape(nwin, win).all(1)


def to_samples(win_mask, win, n):
    """mask ระดับหน้าต่าง -> ระดับตัวอย่างยาว n (กลับด้านของ per_window)

    ทุกตัวอย่างในหน้าต่างได้ค่าเดียวกับหน้าต่าง ตัวอย่างท้ายที่ไม่ครบหน้าต่างเป็น False
    """
    out = np.zeros(n, bool)
    out[:len(win_mask) * win] = np.repeat(win_mask, win)
    return out


# ---------------------------------------------------------------- ขั้น 2: ตรวจเวลา

class TimeFixer:
    """ขั้น 2: ไฟล์ทีละชั่วโมง -> ค่าบน grid เวลาสม่ำเสมอที่ความถี่เดิม

    - เรียงเวลา ลบเวลาซ้ำ (เก็บค่าแรก) รวมถึงช่วงที่ไฟล์ติดกันเหลื่อมกัน
    - ช่องว่าง <= max_gap_s: linear interpolate (ข้ามรอยต่อไฟล์ได้ถ้าไฟล์ติดกันจริง)
    - ช่องว่าง > max_gap_s (รวมไฟล์ชั่วโมงที่หาย): ไม่สร้างจุดใน grid = ไม่มีข้อมูล

    push() คืน list ของ (grid_index เริ่มต้น, ค่า (n, 3)) ที่ต่อเนื่อง
    grid_index = round(เวลา * fs) นับจาก 00:00 ของวันแรก

    ต้อง push ไฟล์ตามลำดับเวลา เพราะจำตัวอย่างสุดท้ายของไฟล์ก่อนไว้ต่อกับไฟล์ถัดไป
    """

    def __init__(self, fs, max_gap_s):
        """fs = ความถี่เดิมของเครื่อง (Hz), max_gap_s = ช่องว่างยาวสุดที่ยอมเติมค่า (วินาที)"""
        self.fs = fs
        self.max_gap = max_gap_s
        self.prev_t = None        # เวลาของตัวอย่างสุดท้ายจากไฟล์ก่อน
        self.prev_v = None        # ค่าของตัวอย่างสุดท้ายจากไฟล์ก่อน
        self.next_idx = None      # grid index ถัดไปที่ยังไม่ได้ส่งออก (กันส่งจุดซ้ำ)
        # ตัวนับสำหรับรายงาน QC
        self.n_unsorted = 0       # จำนวนไฟล์ที่เวลาไม่เรียง
        self.n_duplicates = 0     # จำนวนแถวที่เวลาซ้ำ (ถูกลบ)
        self.n_interpolated = 0   # จุดที่เติมในช่องว่าง (> 1.5 sample แต่ <= max_gap_s)
        self.n_long_gaps = 0      # จำนวนช่องว่าง > max_gap_s

    def push(self, t, v):
        """รับ 1 ไฟล์: t = เวลา (วินาทีจาก 00:00 วันแรก), v = ค่า (n, 3)

        คืน list ของ (grid_index เริ่มต้น, ค่าบน grid) แยกเป็นช่วงต่อเนื่องเมื่อเจอช่องว่างยาว
        """
        t = np.asarray(t, np.float64)
        v = np.asarray(v, np.float32)
        if len(t) == 0:
            return []
        # 1) เรียงเวลา (stable = แถวที่เวลาเท่ากันคงลำดับเดิม จึงเก็บค่าแรกได้ในขั้นถัดไป)
        if np.any(np.diff(t) < 0):
            self.n_unsorted += 1
            order = np.argsort(t, kind="stable")
            t, v = t[order], v[order]
        # 2) ลบเวลาซ้ำภายในไฟล์ และแถวที่ไม่ใหม่กว่าตัวอย่างสุดท้ายของไฟล์ก่อน (ไฟล์เหลื่อมกัน)
        keep = np.concatenate([[True], np.diff(t) > 0])
        if self.prev_t is not None:
            keep &= t > self.prev_t
        self.n_duplicates += int((~keep).sum())
        t, v = t[keep], v[keep]
        if len(t) == 0:
            return []
        # 3) ต่อตัวอย่างสุดท้ายของไฟล์ก่อนไว้ข้างหน้า เพื่อให้เติมค่าข้ามรอยต่อไฟล์ได้
        #    (ถ้าห่างกันเกิน max_gap ขั้น 5 จะไม่สร้างจุดในช่องว่างนั้นอยู่ดี)
        if self.prev_t is not None:
            t = np.concatenate([[self.prev_t], t])
            v = np.concatenate([self.prev_v[None], v])
        self.n_long_gaps += int(np.sum(np.diff(t) > self.max_gap))

        # 4) grid index ที่อยู่ในช่วงเวลาของไฟล์นี้ (เริ่มต่อจากที่ส่งออกไปแล้ว)
        #    บวก/ลบ 1e-6 กันเวลาที่ตรง grid พอดีแต่คลาดเคลื่อนจากทศนิยม
        i0 = int(np.ceil(t[0] * self.fs - 1e-6))
        if self.next_idx is not None:
            i0 = max(i0, self.next_idx)
        i1 = int(np.floor(t[-1] * self.fs + 1e-6))
        self.prev_t, self.prev_v = t[-1], v[-1].copy()
        if i1 < i0:
            return []
        self.next_idx = i1 + 1

        # 5) หาตัวอย่างจริงที่ล้อมแต่ละจุด grid (left <= tg < right)
        #    จุด grid ใช้ได้ถ้าตรงกับตัวอย่างจริง หรืออยู่ในช่องว่างที่ไม่เกิน max_gap
        idx = np.arange(i0, i1 + 1)
        tg = idx / self.fs
        j = np.clip(np.searchsorted(t, tg + 1e-9, side="right"), 1, len(t) - 1)
        left, right = t[j - 1], t[j]
        exact = np.abs(tg - left) < 1e-6
        at_end = np.abs(tg - t[-1]) < 1e-6   # จุดสุดท้ายพอดี (ไม่มี right ถัดไป)
        gap = right - left
        ok = exact | at_end | (gap <= self.max_gap)
        # นับเฉพาะจุดที่เติมในช่องว่างจริง ไม่นับการขยับเล็กน้อยเพราะเวลาไม่ตรง grid (jitter)
        self.n_interpolated += int(np.sum(ok & ~exact & (gap > 1.5 / self.fs)))

        # 6) linear interpolate ทุกแกน แล้วตัดเป็นช่วงต่อเนื่องตาม ok
        vals = np.empty((len(idx), 3), np.float32)
        for k in range(3):
            vals[:, k] = np.interp(tg, t, v[:, k])
        s, e = runs(ok)
        return [(int(idx[a]), vals[a:b]) for a, b in zip(s, e)]


# ---------------------------------------------------------------- ขั้น 4: resample

class Resampler:
    """ขั้น 4: resample ช่วงต่อเนื่องจาก fs_in เป็น fs_out ด้วย resample_poly ทีละก้อน

    ช่วงต่อเนื่องที่ยาวหลายวันจะถูกประมวลผลทีละ chunk_s วินาที โดยมี context_s ทั้งสองข้าง
    กันผลจากขอบ จึงใช้ RAM คงที่ ช่วงไม่มีข้อมูลยังคงเป็นช่องว่าง (ไม่ต่อเข้าหากัน)
    ต้นแต่ละช่วงถูกเลื่อนไปที่ index ที่หารด้วย down ลงตัว (ทิ้ง < 0.1 วินาที)
    เพื่อให้ตัวอย่างที่ได้ตรงกับ grid ของ fs_out พอดี

    วิธีใช้: push() ช่วงจาก TimeFixer ตามลำดับเวลา -> assemble() ได้ timeline 50 Hz ทั้งคน
    """

    def __init__(self, fs_in, fs_out, chunk_s=600, context_s=5, min_run_s=1.0):
        """chunk_s = ขนาดก้อนที่ resample ต่อครั้ง, context_s = ข้อมูลส่วนเกินสองข้างกันผลจากขอบ,
        min_run_s = ช่วงต่อเนื่องที่สั้นกว่านี้ทิ้ง (นับเป็นไม่มีข้อมูล)"""
        # อัตราส่วนเป็นจำนวนเต็มที่ตัดทอนแล้ว: 100 -> 50 = 1/2, 80 -> 50 = 5/8
        g = gcd(int(fs_in), int(fs_out))
        self.up, self.down = int(fs_out) // g, int(fs_in) // g
        self.fs_out = fs_out
        # ปัดขนาดก้อนและ context ให้หารด้วย down ลงตัว ตำแหน่งตัดก้อนจะได้ตรง grid ของ fs_out
        rnd = lambda x: int(ceil(x / self.down)) * self.down
        self.chunk = rnd(chunk_s * fs_in)
        self.ctx = rnd(context_s * fs_in)
        self.min_run = int(min_run_s * fs_in)
        self.pieces = []          # ผลลัพธ์ (index เริ่มที่ fs_out, ค่า) ที่ resample แล้ว
        self.n_short_runs = 0     # จำนวนช่วงที่สั้นเกินจนถูกทิ้ง (รายงาน QC)
        self.run_start = None     # index เริ่ม (ที่ fs_in) ของช่วงที่กำลังสะสม; None = ยังไม่มี

    def push(self, start, vals):
        """รับช่วงต่อเนื่อง 1 ช่วงที่ fs_in (start = grid index, vals = (n, 3))

        ถ้าต่อกับช่วงก่อนพอดี -> สะสมต่อ, ถ้าไม่ต่อ -> ปิดช่วงเดิมแล้วเริ่มช่วงใหม่
        เมื่อสะสมได้เกิน 1 ก้อน + context จะ resample ส่วนที่พร้อมออกไปทันที
        """
        if self.run_start is not None and start == self.run_start + self.run_len:
            self.buf.append(vals)
            self.run_len += len(vals)
        else:
            self.close()
            # เลื่อนจุดเริ่มไปที่ index ที่หารด้วย down ลงตัว (ทิ้งไม่เกิน down - 1 ตัวอย่าง)
            skip = (-start) % self.down
            if skip >= len(vals):
                return
            self.run_start, self.run_len = start + skip, len(vals) - skip
            # buf = ข้อมูลที่ยังเก็บไว้, buf_off = ตำแหน่ง (นับในช่วง) ของ buf[0],
            # emitted = ตำแหน่ง (นับในช่วง) ที่ resample ส่งออกไปแล้วถึงตรงนั้น
            self.buf, self.buf_off, self.emitted = [vals[skip:]], 0, 0
        while self.run_len - self.emitted >= self.chunk + self.ctx:
            self._emit(self.emitted, self.emitted + self.chunk, final=False)

    def close(self):
        """ปิดช่วงที่กำลังสะสม: resample ส่วนที่เหลือจนถึงปลายช่วง (หรือทิ้งถ้าช่วงสั้นเกิน)"""
        if self.run_start is None:
            return
        if self.run_len < self.min_run and self.emitted == 0:
            self.n_short_runs += 1
        elif self.emitted < self.run_len:
            self._emit(self.emitted, self.run_len, final=True)
        self.run_start = None
        self.buf = []

    def _emit(self, a, b, final):
        """resample ส่วน [a, b) ของช่วง (นับเป็นตัวอย่างที่ fs_in) แล้วเก็บผลลง self.pieces

        ใช้ข้อมูลกว้างกว่า [a, b) ข้างละ ctx เพื่อให้ filter ที่ขอบได้ค่าเหมือน resample ทั้งช่วงทีเดียว
        final = True คือส่วนสุดท้ายของช่วง (ไม่มีข้อมูลด้านขวาให้ใช้เป็น context)
        """
        data = np.concatenate(self.buf) if len(self.buf) > 1 else self.buf[0]
        lo = max(self.buf_off, a - self.ctx)
        hi = self.run_len if final else b + self.ctx
        seg = data[lo - self.buf_off:hi - self.buf_off]
        # padtype="line": ต่อขอบด้วยเส้นตรง ค่าคงที่ / offset ผ่าน filter ได้ไม่เพี้ยนที่ขอบ
        y = resample_poly(seg, self.up, self.down, axis=0, padtype="line")
        # เลือกเฉพาะผลที่ตรงกับ [a, b) (ตัด context ทิ้ง)
        # ส่วนสุดท้ายเก็บถึงตัวอย่างที่ไม่เลยเวลาของตัวอย่างจริงตัวสุดท้าย
        ja = (a - lo) * self.up // self.down
        jb = ((b - 1 - lo) * self.up // self.down + 1) if final else (b - lo) * self.up // self.down
        out0 = (self.run_start + a) * self.up // self.down   # index เริ่มที่ fs_out
        self.pieces.append((out0, y[ja:jb].astype(np.float32)))
        self.emitted = b
        # เก็บไว้แค่ส่วนที่ยังต้องใช้เป็น context ของก้อนถัดไป (คุม RAM)
        keep_from = max(self.buf_off, b - self.ctx)
        self.buf = [data[keep_from - self.buf_off:]]
        self.buf_off = keep_from

    def assemble(self, align):
        """รวมทุกช่วงเป็น timeline เดียว เริ่มที่ index 0 ยาวเป็นจำนวนเต็มเท่าของ align

        ช่วงที่ไม่มีข้อมูลเป็น NaN คืน None ถ้าไม่มีข้อมูลเลย
        (run.py ใช้ align = 1 ชั่วโมง ทำให้หน้าต่าง 10 วิ / 15 นาที / 1 ชม. ลงตัวพอดี)
        """
        self.close()
        if not self.pieces:
            return None
        end = max(s + len(p) for s, p in self.pieces)
        n = int(ceil(end / align)) * align
        xyz = np.full((n, 3), np.nan, np.float32)
        while self.pieces:   # pop ทีละก้อน คืน RAM ระหว่างรวม
            s, p = self.pieces.pop()
            xyz[s:s + len(p)] = p
        return xyz


# ---------------------------------------------------------------- ขั้น 3: calibrate (van Hees 2014)

def estimate_calibration(xyz, fs, c, exclude=None):
    """หา offset / scale ต่อแกนจากหน้าต่างนิ่ง ให้ |acc| = 1 g (แบบ GGIR g.calibrate, ไม่ใช้อุณหภูมิ)

    ไม่ใช้หน้าต่างที่ |acc| ห่างจาก 1 g เกิน max_norm_dev_g (เซ็นเซอร์เสีย ไม่ใช่แค่ calibrate เพี้ยน)
    และหน้าต่างที่อยู่ใน exclude (เช่นช่วงที่แหล่งข้อมูลบอกว่าผิดปกติ)
    คืน dict ที่มี offset, scale (ใช้เป็น (xyz + offset) * scale) และ error ก่อน/หลัง (mg) ของจุดที่ใช้
    ถ้าจุดนิ่งไม่พอหรือไม่กระจายรอบทรงกลม -> ไม่ปรับ (offset 0, scale 1)

    หลักการ: ตอนข้อมือนิ่ง เครื่องวัดได้แค่แรงโน้มถ่วง ขนาดต้องเป็น 1 g ไม่ว่าวางมุมไหน
    ถ้าเครื่องเพี้ยน จุดนิ่งจะไม่อยู่บนทรงกลมรัศมี 1 -> หา offset/scale ที่ดึงจุดกลับมาบนทรงกลม
    """
    # หน้าต่างนิ่ง: ข้อมูลครบ, ไม่อยู่ในช่วงที่ต้องตัดออก, SD ทุกแกน < sd_max_g
    win = int(c["window_s"] * fs)
    st = window_stats(xyz, win)
    full = st["n"] == win
    if exclude is not None:
        full &= ~exclude[:len(full) * win].reshape(-1, win).any(1)
    mean, sd = stats_mean(st)[full], stats_sd(st)[full]
    still = (sd < c["sd_max_g"]).all(1) & (np.abs(mean) < 2).all(1)
    out_dev = np.abs(np.linalg.norm(mean, axis=1) - 1) > c["max_norm_dev_g"]
    m = mean[still & ~out_dev]   # ค่าเฉลี่ย 3 แกนของหน้าต่างนิ่งที่ใช้ calibrate
    out = {"cal_points": int(len(m)), "cal_points_outlier": int(np.sum(still & out_dev)),
           "cal_offset": [0.0] * 3, "cal_scale": [1.0] * 3}
    if len(m) == 0:
        return {**out, "cal_status": "no_still_points",
                "cal_error_before_mg": np.nan, "cal_error_after_mg": np.nan}
    # error = ค่าเฉลี่ยของ | |acc| - 1 g | ของจุดนิ่ง หน่วย mg
    err = lambda p: float(np.mean(np.abs(np.linalg.norm(p, axis=1) - 1)) * 1000)
    before = err(m)
    out.update(cal_error_before_mg=before, cal_error_after_mg=before)
    # จุดนิ่งต้องกระจายทั้งด้าน + และ - ของทุกแกน ไม่อย่างนั้นแยก offset กับ scale ออกจากกันไม่ได้
    sphere = all(m[:, k].min() < -c["sphere_g"] and m[:, k].max() > c["sphere_g"] for k in range(3))
    if len(m) < c["min_points"] or not sphere:
        return {**out, "cal_status": "too_few_points" if len(m) < c["min_points"] else "not_sphere"}

    # วนปรับ (iterative closest point แบบ GGIR):
    # 1) ปรับจุดด้วยค่าปัจจุบัน 2) หาจุดที่ใกล้สุดบนทรงกลม (ทิศเดิม ยาว 1)
    # 3) fit เส้นตรงต่อแกน closest = a + b * cur แบบถ่วงน้ำหนัก แล้วรวม a, b เข้ากับ offset / scale
    # 4) น้ำหนัก = 1 / ระยะห่างจากทรงกลม (สูงสุด 100) จุดที่ห่างมากมีผลน้อยลง
    offset, scale, w, res_old = np.zeros(3), np.ones(3), np.ones(len(m)), np.inf
    for _ in range(c["max_iter"]):
        cur = (m + offset) * scale
        closest = cur / np.linalg.norm(cur, axis=1)[:, None]
        sw = np.sqrt(w)
        for k in range(3):
            X = np.c_[np.ones(len(m)), cur[:, k]] * sw[:, None]
            a, b = np.linalg.lstsq(X, closest[:, k] * sw, rcond=None)[0]
            # b * (m + offset) * scale + a = (b * scale) * (m + offset + a / (b * scale))
            offset[k] += a / (scale[k] * b)
            scale[k] *= b
        dist = np.abs(np.linalg.norm((m + offset) * scale, axis=1) - 1)
        res = float(np.sum(w * dist ** 2) / np.sum(w))
        w = np.minimum(1 / np.maximum(dist, 1e-12), 100)
        if abs(res - res_old) < c["tol"]:   # หยุดเมื่อ residual แทบไม่เปลี่ยนแล้ว
            break
        res_old = res
    after = err((m + offset) * scale)
    if after >= before:   # ปรับแล้วไม่ดีขึ้น -> ไม่ใช้ค่าที่หาได้
        return {**out, "cal_status": "not_improved"}
    return {**out, "cal_status": "ok", "cal_offset": offset.tolist(), "cal_scale": scale.tolist(),
            "cal_error_after_mg": after}


def apply_calibration(xyz, cal, block=4_320_000):
    """ปรับ xyz ในที่ (in place) เป็น (xyz + offset) * scale ทีละก้อน (ไม่สร้าง array ใหม่ทั้งก้อน)

    cal = ผลจาก estimate_calibration ค่า NaN ยังเป็น NaN
    """
    off = np.asarray(cal["cal_offset"], np.float32)
    sc = np.asarray(cal["cal_scale"], np.float32)
    for a in range(0, len(xyz), block):
        v = xyz[a:a + block]   # view: แก้ v = แก้ xyz
        v += off
        v *= sc


# ---------------------------------------------------------------- ขั้น 5: clip

def clip_and_flag(xyz, c):
    """clip ที่ ±limit_g (ในที่) และคืน mask ของตัวอย่างที่ |ค่า| >= flag_g ในแกนใดแกนหนึ่ง

    clip ที่ ±6 g ทั้งสองชุดเพราะ ActiGraph (NHANES) วัดได้แค่ ±6 g ส่วน GENEActiv (COPS) วัดได้ ±8 g
    ค่าที่เข้าใกล้ขีดจำกัดถูกติดธง เพราะอาจเป็นค่าที่ถูกตัดเพดานโดยเครื่อง
    """
    flagged = np.zeros(len(xyz), bool)
    for k in range(3):
        v = xyz[:, k]
        with np.errstate(invalid="ignore"):   # NaN เทียบแล้วได้ False ไม่ต้องเตือน
            flagged |= np.abs(v) >= c["flag_g"]
        np.clip(v, -c["limit_g"], c["limit_g"], out=v)
    return flagged


# ---------------------------------------------------------------- ขั้น 6: สัญญาณผิดปกติ

def abnormal_mask(xyz, fs, c, intervals=None):
    """ค่าค้าง (ทุกแกนไม่เปลี่ยน) นาน >= stuck_min_s, |acc| ~ 0 นาน >= zero_min_s,
    spike ตัวอย่างเดียวที่กระโดด > spike_g แล้วกลับ, และช่วงเวลาที่ reader ส่งมา (เช่น Logs ของ NHANES)"""
    n = len(xyz)
    still = np.ones(n - 1, bool)       # ตัวอย่างที่ i กับ i+1 เท่ากันทุกแกน
    near_zero = np.ones(n, bool)       # ทุกแกนใกล้ 0 (ข้อมือจริงต้องมีแรงโน้มถ่วง ~1 g เสมอ)
    spike = np.zeros(n, bool)
    with np.errstate(invalid="ignore"):
        for k in range(3):
            v = xyz[:, k]
            still &= np.abs(np.diff(v)) < c["stuck_eps_g"]
            near_zero &= np.abs(v) < c["zero_g"]
            # spike = ต่างจากทั้งตัวก่อนและตัวถัดไปเกิน spike_g ไปทางเดียวกัน (กระโดดแล้วกลับ)
            d1 = v[1:-1] - v[:-2]
            d2 = v[1:-1] - v[2:]
            spike[1:-1] |= (np.abs(d1) > c["spike_g"]) & (np.abs(d2) > c["spike_g"]) & (np.sign(d1) == np.sign(d2))
    # still นับเป็นคู่ตัวอย่าง: n คู่ติดกัน = n + 1 ตัวอย่าง จึงลบ 1 และติดธงทั้งตัวต้นและตัวท้ายของคู่
    stuck = keep_long_runs(still, int(c["stuck_min_s"] * fs) - 1)
    out = np.zeros(n, bool)
    out[:-1] |= stuck
    out[1:] |= stuck
    out |= keep_long_runs(near_zero, int(c["zero_min_s"] * fs))
    # ติดธงรอบ spike ข้างละ spike_pad_s ด้วย (filter ตอน resample กระจาย spike ไปตัวอย่างข้างเคียง)
    pad = int(round(c["spike_pad_s"] * fs))
    s = np.flatnonzero(spike)
    out |= fill_runs(n, s - pad, s + pad + 1)
    if intervals is not None:
        out |= intervals_mask(n, fs, intervals)
    return out


def intervals_mask(n, fs, intervals):
    """ช่วงเวลา [start, end] วินาที -> mask ยาว n ตัวอย่าง (ขยายขอบออกให้ครอบทั้งช่วง)"""
    iv = np.asarray(intervals, float).reshape(-1, 2)
    return fill_runs(n, np.floor(iv[:, 0] * fs).astype(int), np.ceil(iv[:, 1] * fs).astype(int) + 1)


def impossible_gravity_mask(xyz, fs, c):
    """แรงโน้มถ่วงเป็นไปไม่ได้ = เซ็นเซอร์เสีย (ตรวจได้ทั้งตอนนิ่งและตอนขยับ)

    - หน้าต่างนิ่ง (SD ทุกแกน < gravity_sd_max_g) ที่ |ค่าเฉลี่ย| ห่างจาก 1 g เกิน gravity_tol_g
    - ชั่วโมงที่มัธยฐานของ |acc| (ทีละตัวอย่าง) ห่างจาก 1 g เกิน gravity_hour_tol_g
      (ข้อมือคนอยู่ที่ ~1 g เกือบตลอดแม้ตอนขยับ มัธยฐานรายชั่วโมงของคนปกติ ~1.00 g
       ไม่ใช้ |ค่าเฉลี่ยเวกเตอร์| เพราะตอนหมุนข้อมือแรง ๆ เวกเตอร์เฉลี่ยสั้นลงทั้งที่เซ็นเซอร์ปกติ)
    ตัวอย่าง: NHANES-74132 วันที่ 6-8 มัธยฐาน |acc| 1.3-2.0 g ทั้งวัน
    """
    # แบบที่ 1: หน้าต่างนิ่งที่แรงโน้มถ่วงผิด
    win = int(c["gravity_window_s"] * fs)
    st = window_stats(xyz, win)
    mean, sd = stats_mean(st), stats_sd(st)
    norm = np.linalg.norm(mean, axis=1)
    bad = (st["n"] == win) & (sd < c["gravity_sd_max_g"]).all(1) & (np.abs(norm - 1) > c["gravity_tol_g"])

    # แบบที่ 2: ทั้งชั่วโมงที่มัธยฐาน |acc| ผิด (คำนวณทีละชั่วโมงเพื่อประหยัด RAM)
    hour = int(3600 * fs)
    nh = len(xyz) // hour
    med = np.full(nh, np.nan)
    n_ok = np.zeros(nh, np.int64)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # ชั่วโมงที่ไม่มีข้อมูลเลย
        for h in range(nh):
            mag = np.linalg.norm(xyz[h * hour:(h + 1) * hour], axis=1)
            n_ok[h] = np.sum(~np.isnan(mag))
            med[h] = np.nanmedian(mag)
    enough = n_ok >= c["gravity_hour_min_windows"] * win   # ต้องมีข้อมูลพอในชั่วโมงนั้น
    bad_hour = enough & (np.abs(med - 1) > c["gravity_hour_tol_g"])
    return to_samples(bad, win, len(xyz)) | to_samples(bad_hour, hour, len(xyz))


# ---------------------------------------------------------------- ขั้น 7: ช่วงนิ่ง (ISM) + ปรับ noise

def ism_mask(xyz, fs, c):
    """ช่วงนิ่ง 2 แบบ (ใช้กฎเดียวกันทุก dataset)
    - หน้าต่าง window_s วินาที (ตาม grid เวลา) ที่ทุกแกนมี max-min <= range_max_g
    - หน้าต่าง constant_window_s ที่ทุกแกน SD < constant_sd_max_g: นิ่งกว่า noise ของเซ็นเซอร์จริง
      = เครื่องหยุดวัดแล้วเขียนค่าเดิมซ้ำ (idle sleep mode) จับช่วงที่เริ่มกลางหน้าต่าง 10 วินาทีได้

    ActiGraph (NHANES) หยุดวัดเมื่อนิ่ง 10 วินาที แต่ GENEActiv (COPS) วัดตลอด
    ตัดช่วงนิ่งทิ้งทั้งสองชุดด้วยกฎเดียวกัน โมเดลจะได้ไม่เรียนความต่างของเครื่องตอนนิ่ง
    """
    # แบบที่ 1: หน้าต่าง 10 วิ ที่ทุกแกนเปลี่ยนไม่เกิน range_max_g (ต้องมีข้อมูล >= min_valid_frac)
    win = int(c["window_s"] * fs)
    st = window_stats(xyz, win)
    ok = st["n"] >= c["min_valid_frac"] * win
    still = ok & ((st["max"] - st["min"]) <= c["range_max_g"]).all(1)
    out = to_samples(still, win, len(xyz))
    # แบบที่ 2: หน้าต่าง 1 วิ ที่นิ่งสนิทจนเป็นค่าคงที่
    cw = int(c["constant_window_s"] * fs)
    st = window_stats(xyz, cw)
    const = (st["n"] == cw) & (stats_sd(st) < c["constant_sd_max_g"]).all(1)
    return out | to_samples(const, cw, len(xyz))


def noise_floor_mg(xyz, fs, window_s, mask, min_windows):
    """มัธยฐานของ SD (เฉลี่ย 3 แกน) ในหน้าต่าง window_s ที่ข้อมูลครบและอยู่ใน mask ทั้งหน้าต่าง

    ใช้วัดระดับ noise ของเซ็นเซอร์ตอนนาฬิกาวางนิ่ง (mask = ช่วงถอดนาฬิกา หรือช่วง ISM)
    คืน (noise floor เป็น mg, จำนวนหน้าต่างที่ใช้) ถ้าหน้าต่างน้อยกว่า min_windows คืน NaN
    """
    win = int(window_s * fs)
    st = window_stats(xyz, win)
    sel = (st["n"] == win) & per_window(mask, win)
    if sel.sum() < min_windows:
        return np.nan, int(sel.sum())
    return float(np.median(stats_sd(st)[sel].mean(1)) * 1000), int(sel.sum())


def noise_floor_axes_mg(xyz, fs, window_s, mask, min_windows):
    """เหมือน noise_floor_mg แต่แยกแกน: มัธยฐานของ SD แต่ละแกน -> array (x, y, z) เป็น mg

    GENEActiv มี noise แต่ละแกนไม่เท่ากัน (z สูงกว่า x ~1 mg) จึงต้องเทียบ / เติม noise ทีละแกน
    ถ้าหน้าต่างน้อยกว่า min_windows คืน NaN ทั้ง 3 แกน
    """
    win = int(window_s * fs)
    st = window_stats(xyz, win)
    sel = (st["n"] == win) & per_window(mask, win)
    if sel.sum() < min_windows:
        return np.full(3, np.nan), int(sel.sum())
    return np.median(stats_sd(st)[sel], 0) * 1000, int(sel.sum())


def quantize(xyz, step_g, block=4_320_000):
    """ปัดทุกค่าเป็นขั้นละ step_g (ในที่) เช่น 3.9 mg = ความละเอียดของ GENEActiv

    ทำกับทั้งสองชุด หลังเติม noise ให้ค่าของทั้งสองเครื่องมีความละเอียดเท่ากัน
    (ไม่อย่างนั้นโมเดลแยกเครื่องได้จากว่าค่าตกบนขั้น 3.9 mg หรือไม่)
    """
    for a in range(0, len(xyz), block):
        v = xyz[a:a + block]
        np.multiply(np.round(v / step_g), step_g, out=v)


def add_noise(xyz, sigma_g, seed, block=4_320_000):
    """เติม Gaussian noise SD = sigma_g (ในที่) ทีละก้อน
    sigma_g เป็นตัวเลขเดียว (ทุกแกนเท่ากัน) หรือ 3 ค่า (x, y, z) ก็ได้

    ใช้กับ NHANES ให้ noise ตอนนิ่งเท่ากับ COPS ทีละแกน seed คงที่ต่อคน = รันซ้ำได้ผลเดิม
    """
    rng = np.random.default_rng(seed)
    sigma = np.broadcast_to(np.asarray(sigma_g, np.float32), (3,))
    for a in range(0, len(xyz), block):
        v = xyz[a:a + block]
        v += rng.standard_normal(v.shape, dtype=np.float32) * sigma


# ---------------------------------------------------------------- ขั้น 8: ถอดนาฬิกา

def nonwear_mask(xyz, fs, c):
    """หน้าต่าง window_min นาที เลื่อนทีละ step_min: ถอดถ้า SD < sd_max_g และ range < range_max_g
    ใน >= min_axes แกน ตัวอย่างที่อยู่ในหน้าต่างถอดใด ๆ ถูกติดธง

    คำนวณสถิติต่อก้อน step_min นาทีครั้งเดียว แล้วรวม k ก้อนติดกันเป็นหน้าต่าง window_min นาที
    (60 นาที เลื่อนทีละ 15 = รวม 4 ก้อน) ไม่ต้องอ่านข้อมูลซ้ำทุกหน้าต่าง

    SD ใช้ค่าดิบ แต่ range ใช้ค่าเฉลี่ยทุก range_smooth_s วินาที:
    range ของค่าดิบใน 1 ชม. (180,000 ตัวอย่าง) ถูก noise ดันขึ้นไปเอง (noise SD 5.45 mg -> range ~50 mg
    ผ่านเกณฑ์แค่ ~56%) ทำให้การจับถอดขึ้นกับระดับ noise ของเครื่อง ค่าเฉลี่ย 1 วิ ตัด noise เหลือ < 1 mg
    แต่การขยับจริงยังเห็นชัด
    """
    blk = int(c["step_min"] * 60 * fs)   # ตัวอย่างต่อก้อน
    k = int(c["window_min"] // c["step_min"])   # ก้อนต่อหน้าต่าง
    st = window_stats(xyz, blk)
    nb = len(st["n"])
    if nb < k:
        return np.zeros(len(xyz), bool)
    # min / max ต่อก้อน จากค่าเฉลี่ยทุก range_smooth_s วินาที (แทนค่าดิบ)
    sw = int(c["range_smooth_s"] * fs)
    smooth = stats_mean(window_stats(xyz, sw))   # (จำนวนช่วง, 3) NaN = ช่วงไม่มีข้อมูล
    per_blk = blk // sw
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # ก้อนที่ไม่มีข้อมูลเลย
        sm = smooth[:nb * per_blk].reshape(nb, per_blk, 3)
        st["min"], st["max"] = np.nanmin(sm, 1), np.nanmax(sm, 1)

    def slide(a):
        """ผลรวมของ k ก้อนติดกัน ทุกตำแหน่ง (ผ่านผลรวมสะสม)"""
        cs = np.cumsum(np.concatenate([np.zeros((1,) + a.shape[1:]), a]), axis=0)
        return cs[k:] - cs[:-k]

    # รวมสถิติของ k ก้อน -> สถิติของหน้าต่าง window_min นาที
    n = slide(st["n"].astype(float))
    s, ss = slide(st["sum"]), slide(st["sumsq"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mn = np.nanmin(np.lib.stride_tricks.sliding_window_view(st["min"], k, axis=0), axis=-1)
        mx = np.nanmax(np.lib.stride_tricks.sliding_window_view(st["max"], k, axis=0), axis=-1)
        var = (ss - s ** 2 / n[:, None]) / (n[:, None] - 1)
    sd = np.sqrt(np.clip(var, 0, None))
    # แต่ละแกน "นิ่ง" ถ้า SD (ค่าดิบ) และ range (ค่าเฉลี่ย 1 วิ) ต่ำทั้งคู่; ถอดถ้านิ่ง >= min_axes แกนและมีข้อมูลพอ
    axis_still = (sd < c["sd_max_g"]) & ((mx - mn) < c["range_max_g"])
    win_nw = (n >= c["min_valid_frac"] * k * blk) & (axis_still.sum(1) >= c["min_axes"])
    # หน้าต่างที่เริ่มที่ก้อน j ครอบก้อน j..j+k-1 -> ก้อนใดอยู่ในหน้าต่างถอดอย่างน้อย 1 หน้าต่าง = ถอด
    block_nw = np.convolve(win_nw.astype(int), np.ones(k, int))[:nb] > 0
    return to_samples(block_nw, blk, len(xyz))


# ---------------------------------------------------------------- ขั้น 9: นอน (HDCZA, van Hees 2018)

def hdcza(xyz, fs, c):
    """หาช่วงนอน (sleep period time) คืนละ 1 ช่วง แบบ HDCZA ของ GGIR

    1. ค่าเฉลี่ยแต่ละแกนต่อ epoch (epoch_s) -> มุมแกน z = atan(z / sqrt(x^2 + y^2))
       (GGIR ใช้ rolling median 5 วินาทีแล้วเฉลี่ยมุมต่อ epoch; ตรงนี้ใช้ค่าเฉลี่ยต่อ epoch แทน
        ไม่ใช้มัธยฐาน เพราะมัธยฐานของข้อมูลที่ปัดเป็นขั้น (COPS) ในช่วงนิ่งได้ค่าเดิมทุก epoch
        ทำให้ threshold ของสองชุดต่างกันเพราะเครื่อง ไม่ใช่เพราะคน)
    2. |ผลต่างมุมของ epoch ติดกัน| -> rolling median median_window_min นาที
    3. ต่อหน้าต่างเที่ยงวัน-เที่ยงวัน: threshold = percentile * multiplier (จำกัดใน [min, max] องศา)
    4. ช่วงต่ำกว่า threshold ที่ยาว > min_block_min, เชื่อมช่องว่าง < max_gap_min, เลือกช่วงยาวสุด

    หลักการ: ตอนนอนมุมข้อมือแทบไม่เปลี่ยนเป็นเวลานาน (พลิกตัวเป็นครั้งคราว)
    คืน (mask ช่วงนอน, list ข้อมูลรายคืน: เริ่ม/จบเป็นวินาที, จำนวนชั่วโมง, threshold ที่ใช้)
    """
    # 1) ค่าเฉลี่ยต่อ epoch ทีละวัน (epoch ที่ขาดข้อมูลเกินครึ่ง = NaN)
    ep = int(c["epoch_s"] * fs)
    nep = len(xyz) // ep
    med = np.full((nep, 3), np.nan)
    step = 86400 // int(c["epoch_s"])   # จำนวน epoch ต่อวัน
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for a in range(0, nep, step):
            b = min(nep, a + step)
            w = xyz[a * ep:b * ep].reshape(b - a, ep, 3)
            m = np.nanmean(w, axis=1)
            m[np.isnan(w[..., 0]).sum(1) > ep // 2] = np.nan
            med[a:b] = m
    angle = np.degrees(np.arctan(med[:, 2] / np.sqrt(med[:, 0] ** 2 + med[:, 1] ** 2)))
    # 2) มุมเปลี่ยนมากแค่ไหนระหว่าง epoch ติดกัน แล้วทำให้เรียบด้วย rolling median
    diff = np.abs(np.diff(angle, prepend=np.nan))
    rw = int(c["median_window_min"] * 60 / c["epoch_s"])
    rm = pd.Series(diff).rolling(rw, center=True, min_periods=rw // 2).median().to_numpy()

    sleep = np.zeros(nep, bool)
    nights = []
    half = step // 2
    # 3) หน้าต่าง k = เที่ยงวันของวัน k-1 ถึงเที่ยงวันของวัน k (ครอบคืนเดียว)
    for k in range(nep // step + 2):
        a, b = max(0, k * step - half), min(nep, k * step + half)
        seg = rm[a:b]
        okv = ~np.isnan(seg)
        if b <= a or okv.sum() * c["epoch_s"] / 3600 < c["min_window_hours"]:
            continue   # ข้อมูลในหน้าต่างน้อยเกินไป
        # threshold ปรับตามคนแต่ละคืน: percentile ต่ำ ๆ ของการเปลี่ยนมุม x multiplier
        thr = float(np.clip(np.nanpercentile(seg, c["percentile"]) * c["multiplier"],
                            c["threshold_min_deg"], c["threshold_max_deg"]))
        # 4) ช่วงที่มุมแทบไม่เปลี่ยน -> เก็บเฉพาะช่วงยาว -> เชื่อมช่วงที่ห่างกันไม่มาก
        s, e = runs(okv & (seg < thr))
        long = (e - s) * c["epoch_s"] > c["min_block_min"] * 60
        blocks = []
        for bs, be in zip(s[long], e[long]):
            if blocks and (bs - blocks[-1][1]) * c["epoch_s"] < c["max_gap_min"] * 60:
                blocks[-1][1] = be
            else:
                blocks.append([bs, be])
        if not blocks:
            continue
        # ช่วงที่ยาวที่สุดของคืนนี้ = ช่วงนอน
        bs, be = max(blocks, key=lambda x: x[1] - x[0])
        sleep[a + bs:a + be] = True
        nights.append({"window": k, "start_s": (a + bs) * c["epoch_s"], "end_s": (a + be) * c["epoch_s"],
                       "hours": round((be - bs) * c["epoch_s"] / 3600, 2), "threshold_deg": round(thr, 3)})
    return to_samples(sleep, ep, len(xyz)), nights


def compare_hourly(pred, recorded, label, fs):
    """เทียบธงนอนรายชั่วโมง (>= 50% ของชั่วโมง) กับ label ของ diary (1 = นอน, 0 = ตื่น, NaN = ไม่มี)

    pred = mask ช่วงนอนจาก hdcza, recorded = mask ตัวอย่างที่มีข้อมูล, label = diary รายชั่วโมง
    ใช้เฉพาะชั่วโมงที่มีข้อมูล >= 50% และ diary มีค่า
    คืน accuracy, sensitivity (จับนอนได้), specificity (จับตื่นได้), Cohen's kappa
    """
    hour = int(3600 * fs)
    nh = min(len(pred) // hour, len(label))
    rec = recorded[:nh * hour].reshape(nh, hour).mean(1)   # สัดส่วนที่มีข้อมูลต่อชั่วโมง
    frac = (pred & recorded)[:nh * hour].reshape(nh, hour).sum(1) / np.maximum(rec * hour, 1)
    lab = np.asarray(label[:nh], float)
    sel = (rec >= 0.5) & ~np.isnan(lab)
    p, t = frac[sel] >= 0.5, lab[sel] == 1
    tp, tn = int(np.sum(p & t)), int(np.sum(~p & ~t))
    fp, fn = int(np.sum(p & ~t)), int(np.sum(~p & t))
    n = tp + tn + fp + fn
    if n == 0:
        return {"sleep_diary_hours": 0}
    acc = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (tn + fn) * (tn + fp)) / n ** 2   # ความตรงกันโดยบังเอิญ (ใช้ใน kappa)
    return {
        "sleep_diary_hours": n,
        "sleep_accuracy": round(acc, 3),
        "sleep_sensitivity": round(tp / (tp + fn), 3) if tp + fn else np.nan,
        "sleep_specificity": round(tn / (tn + fp), 3) if tn + fp else np.nan,
        "sleep_kappa": round((acc - pe) / (1 - pe), 3) if pe < 1 else np.nan,
    }


# ---------------------------------------------------------------- ขั้น 11: ตัดสินวัน

def day_hours(mask, fs):
    """จำนวนชั่วโมงที่ mask เป็น True ในแต่ละวัน (วันแบ่งที่เที่ยงคืน) คืน array ยาว = จำนวนวัน

    ใช้นับชั่วโมงที่ใช้ได้ต่อวัน (วันที่ใช้ได้ต้อง >= min_valid_hours) และชั่วโมงที่มีข้อมูลต่อวัน
    """
    per_day = int(86400 * fs)
    starts = np.arange(0, len(mask), per_day)
    # view เป็น uint8 แล้วรวมทีละวัน (reduceat) ไม่ต้องสร้าง array ใหม่
    return np.add.reduceat(mask.view(np.uint8), starts, dtype=np.int64) / fs / 3600


def flag_names(flags):
    """uint16 bitmask -> ชื่อธงคั่นด้วย '|' (None ถ้าไม่มีธง) เช่น BIT["ism"] | BIT["sleep"] -> "ism|sleep"

    ใช้ตอนเขียนคอลัมน์ reason ใน parquet
    """
    flags = int(flags)
    if flags == 0:
        return None
    return "|".join(f for f in FLAGS if flags & int(BIT[f]))

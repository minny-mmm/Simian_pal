"""ตรวจเบื้องต้นว่า feature หลักไปในทิศที่คาดไหม (PD vs ปกติ) จากไฟล์ดิบที่มีในเครื่อง
ใช้แค่ numpy + pandas ยังไม่ได้ clean เต็มขั้น (ไม่ calibrate / ไม่ resample / ใช้ 08-22 น. แทนตัวหาช่วงนอน)

    python3 tools/feature_check.py COPS-8 COPS-29 COPS-45 74132

ได้ reports/feature_check_hours.csv (1 แถว = 1 ชั่วโมง) และพิมพ์สรุปรายคน
"""
import glob
import io
import os
import sys
import tarfile
import zipfile

import numpy as np
import pandas as pd

WIN_S = 10
STILL_G = 0.05          # ทุกแกนเปลี่ยนไม่เกิน 50 mg ใน 10 วิ = นิ่ง (ตัดทิ้งทั้งสองชุด)
HOURS = range(8, 22)    # ใช้ช่วง 08:00-21:59 แทนช่วงตื่น
TREMOR_BAND = (3.5, 7.5)
REF_BAND = (1.0, 15.0)


def movavg(a, n):
    c = np.cumsum(np.insert(a, 0, 0, axis=0), axis=0)
    out = (c[n:] - c[:-n]) / n
    pad = n - 1
    return np.concatenate([np.repeat(out[:1], pad // 2, 0), out, np.repeat(out[-1:], pad - pad // 2, 0)])


def window_features(xyz, fs):
    n = int(WIN_S * fs)
    rows = []
    freqs = np.fft.rfftfreq(n, 1 / fs)
    tb = (freqs >= TREMOR_BAND[0]) & (freqs <= TREMOR_BAND[1])
    rb = (freqs >= REF_BAND[0]) & (freqs <= REF_BAND[1])
    lag_lo, lag_hi = int(0.35 * fs), int(1.3 * fs)   # จังหวะก้าวหรือรอบแกว่งแขน 0.75-2.9 Hz
    hann = np.hanning(n)[:, None]
    for i in range(len(xyz) // n):
        w = xyz[i * n:(i + 1) * n]
        if np.isnan(w).any():
            continue
        rng = w.max(0) - w.min(0)
        still = bool((rng <= STILL_G).all())
        mag = np.linalg.norm(w, axis=1)
        r = dict(still=still, enmo=float(np.maximum(mag - 1, 0).mean()))
        if still:
            rows.append(r)
            continue
        # มุมแขน: เทียบทิศแรงโน้มถ่วง (เฉลี่ย 1 วิ) กับทิศเฉลี่ยของหน้าต่าง -> ไม่ขึ้นกับการวางแกนของแต่ละยี่ห้อ
        g = movavg(w, int(0.25 * fs))
        g /= np.linalg.norm(g, axis=1, keepdims=True)
        gm = g.mean(0)
        gm /= np.linalg.norm(gm)
        ang = np.degrees(np.arccos(np.clip(g @ gm, -1, 1)))
        r["angle_range"] = float(np.percentile(ang, 95) - np.percentile(ang, 5))
        # ความแรงของการขยับ (หักส่วนที่ช้ากว่า 1 วิ) และ jerk
        dyn = w - movavg(w, int(fs))
        r["dyn_rms"] = float(np.sqrt((dyn ** 2).sum(1).mean()))
        r["jerk"] = float(np.abs(np.diff(mag)).mean() * fs)
        # เดิน: autocorrelation ของขนาดแรงที่จังหวะก้าว
        m = w - w.mean(0)
        F = np.fft.rfft(m, 2 * n, axis=0)
        ac = np.fft.irfft((np.abs(F) ** 2).sum(1), 2 * n)[:n]
        ac = ac / ac[0] if ac[0] > 0 else ac
        r["step_ac"] = float(ac[lag_lo:lag_hi].max())
        r["walk"] = bool(r["step_ac"] > 0.5 and r["dyn_rms"] > 0.12)
        # สเปกตรัม (รวม 3 แกน) สำหรับ tremor
        p = (np.abs(np.fft.rfft((w - w.mean(0)) * hann, axis=0)) ** 2).sum(1)
        r["tremor_ratio"] = float(p[tb].sum() / max(p[rb].sum(), 1e-12))
        r["tremor_rms"] = float(np.sqrt(2 * p[tb].sum() / (n * (hann ** 2).sum())))
        r["peak_hz"] = float(freqs[rb][np.argmax(p[rb])])
        r["rest"] = bool(not r["walk"] and r["angle_range"] < 15 and r["dyn_rms"] < 0.08)
        rows.append(r)
    return rows


def summarize_hour(rows, **meta):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    mv = df[~df.still]
    walk = mv[mv.walk] if "walk" in mv else mv.iloc[:0]
    rest = mv[mv.rest] if "rest" in mv else mv.iloc[:0]
    tremor = rest[(rest.tremor_ratio > 0.5) & (rest.tremor_rms > 0.015)] if len(rest) else rest
    return dict(meta,
                windows=len(df), still_frac=df.still.mean(),
                enmo_mg=df.enmo.mean() * 1000,
                walk_windows=len(walk), rest_windows=len(rest), tremor_windows=len(tremor),
                swing_deg=walk.angle_range.median() if len(walk) else np.nan,
                walk_jerk=walk.jerk.median() if len(walk) else np.nan,
                move_jerk=mv.jerk.median() if len(mv) else np.nan,
                rest_tremor_ratio=rest.tremor_ratio.median() if len(rest) else np.nan,
                rest_peak_hz=rest.peak_hz.median() if len(rest) else np.nan)


def cops_wrist(sid, path="metadata/cops_splits.csv"):
    """ข้อมือที่ใช้ (ข้างไม่ถนัด) จาก cops_splits.csv; ไม่มีไฟล์ / ไม่มีคนนี้ -> left พร้อมคำเตือน"""
    if not os.path.exists(path):
        print(f"คำเตือน: ไม่พบ {path} ใช้ข้อมือซ้ายกับ {sid}", file=sys.stderr)
        return "left"
    splits = pd.read_csv(path, dtype=str).set_index("subject_id")
    if sid not in splits.index:
        print(f"คำเตือน: {sid} ไม่มีใน {path} ใช้ข้อมือซ้าย", file=sys.stderr)
        return "left"
    return splits.loc[sid, "use_wrist"].lower()


def cops_hours(sid):
    base = f"data/raw/cops/{sid}"
    d = pd.read_csv(f"{base}/{sid}_symptomdiary.csv", sep=";")
    wrist = cops_wrist(sid)
    col = "WearableDataLeftZIP" if wrist == "left" else "WearableDataRightZIP"
    keep = d[(d.Visit == "outpatient") & (d.WearableDataAvailability == "Wearable Worn")
             & (d.Kinesia != "Sleep") & d[col].notna()]
    for _, row in keep.iterrows():
        path = glob.glob(f"{base}/Accelerometry/{row[col]}")
        if not path:
            continue
        with zipfile.ZipFile(path[0]) as z:
            raw = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), sep=";", usecols=["Time", "X", "Y", "Z"])
        hour = int(str(raw.Time.iloc[0])[:2])
        if hour not in HOURS:
            continue
        yield dict(id=sid, group="PD", day=str(row.Day), hour=hour, tremor_score=row.TremorScore), \
            raw[["X", "Y", "Z"]].to_numpy(np.float32), 100


def nhanes_hours(seqn):
    t = tarfile.open(f"data/raw/nhanes/2013-2014/pax80_raw/{seqn}.tar.bz2", "r|bz2")
    for m in t:
        if not m.name.endswith(".sensor.csv"):
            continue
        stamp = m.name.split(".")[-3]          # 2000-01-05-18-00-00-000-P0000
        day, hour = stamp[:10], int(stamp[11:13])
        if hour not in HOURS:
            continue
        raw = pd.read_csv(io.BytesIO(t.extractfile(m).read()), usecols=["X", "Y", "Z"], dtype=np.float32)
        yield dict(id=str(seqn), group="healthy", day=day, hour=hour, tremor_score=np.nan), raw.to_numpy(), 80


def main(ids):
    os.makedirs("reports/feature_check", exist_ok=True)
    for sid in ids:
        out = []
        gen = cops_hours(sid) if sid.upper().startswith("COPS") else nhanes_hours(sid)
        for meta, xyz, fs in gen:
            s = summarize_hour(window_features(xyz, fs), **meta)
            if s and s["still_frac"] < 0.9:          # ชั่วโมงที่นิ่งเกือบทั้งหมด = น่าจะถอดนาฬิกา
                out.append(s)
        pd.DataFrame(out).to_csv(f"reports/feature_check/{sid}.csv", index=False)
        print(f"{sid}: {len(out)} ชั่วโมง", flush=True)
    h = pd.concat([pd.read_csv(f, dtype={"id": str}) for f in glob.glob("reports/feature_check/*.csv")])
    h.to_csv("reports/feature_check_hours.csv", index=False)
    w = lambda c: (h[c] * h.walk_windows).groupby(h.id).sum() / h.walk_windows.groupby(h.id).sum()
    s = h.groupby(["id", "group"]).agg(hours=("hour", "size"), enmo_mg=("enmo_mg", "mean"),
                                       still_pct=("still_frac", "mean"), move_jerk=("move_jerk", "median"))
    s = s.reset_index().set_index("id")
    tot = h.groupby("id")[["windows", "walk_windows", "rest_windows", "tremor_windows"]].sum()
    s["walk_pct"] = 100 * tot.walk_windows / tot.windows
    s["swing_deg"] = w("swing_deg")
    s["rest_tremor_pct"] = 100 * tot.tremor_windows / tot.rest_windows
    s["rest_tremor_ratio"] = h.groupby("id").rest_tremor_ratio.median()
    s["still_pct"] *= 100
    print(s.round(2).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])

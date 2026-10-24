"""คำนวณค่ารายวันสำหรับ DAILY_ROWS ใน data.py จาก output ของ pipeline (พิมพ์แถวออกมาให้ copy ไปวาง)

  cd <repo>
  python web/backend/database/make_daily_rows.py cops/COPS-7 nhanes/NHANES-81262 nhanes/NHANES-73558
  python web/backend/database/make_daily_rows.py --check <daily.csv ของเดโม> cops/COPS-7 ...   # เทียบกับเดโม

วิธีคำนวณ = เกณฑ์เดียวกับเดโม AI ของทีม (src/reference/daily.py + config/reference_baseline.yaml ใน branch feat/lanna)
ค่าที่ได้จึงตรงกับ daily.csv ใน valid bundle ที่เดโมใช้ (reference-demo-cohort-20261024-v2) ทุกค่า
- วันที่ใช้ = วันที่มีใน data/features/<dataset>/<id>.csv (pipeline คัดวันอยู่บ้าน บันทึกครบ 24 ชม. ไว้แล้ว)
  source_day = เลขวันต้นฉบับ (เริ่ม 0) = ค่า day ที่ API เดโมใช้ (หน้าเดโมแสดง day + 1)
- ใช้เฉพาะชั่วโมง 09–21 น. ที่มีข้อมูลอย่างน้อย 20 นาที; วันใช้ได้ต้องมี >= 6 ชั่วโมงและ >= 360 นาที
- enmo_g   = ค่าเฉลี่ย enmo_median รายชั่วโมง ถ่วงน้ำหนักด้วย n_windows (ต้องมี >= 6 ชม. / 360 นาที)
- cadence  = ค่าเฉลี่ย step_freq_walk_median รายชั่วโมง ถ่วงน้ำหนักด้วย n_walk (steps/s) (ต้องเดิน >= 2 ชม. / 5 นาที)
- tremor   = n_tremor รวม / n_rest รวม (ต้องมีช่วงพัก >= 2 ชม. / 5 นาที)
  ไม่ผ่านเกณฑ์ = None + qc "insufficient_coverage" (เดโมเรียก insufficient_data)
- จำนวน window ที่ใช้คิดค่า (= metric_windows ของเดโม) + เหตุผล QC (= reasons ของเดโม) -> DAILY_WINDOWS ใน data.py
  n_windows (ENMO), n_walk (cadence), n_rest / n_tremor (tremor) ใช้คิดค่าเฉลี่ยหลายวันแบบถ่วงน้ำหนักตามสูตรเดียวกัน
- ชั่วโมงช่วง 09–21 น. (รวม 12) จาก data/clean/<dataset>/<id>.parquet (50 Hz):
  wear = sample ที่ valid, no_signal = ไม่มีข้อมูล + nonwear, low_quality = ที่เหลือ (ism/abnormal/clip/sleep)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[3]
FS = 50   # Hz ของ clean parquet
WINDOW_MIN = 10 / 60   # หน้าต่างละ 10 วินาที
CLOCK = (9, 21)
MIN_HOUR_MIN, MIN_DAY_HOURS, MIN_DAY_MIN = 20, 6, 360
# metric: (คอลัมน์รายชั่วโมง, ตัวถ่วง/ตัวนับ, นาทีขั้นต่ำ, ชั่วโมงขั้นต่ำ)
RULES = {"enmo": ("enmo_median", "n_windows", 360, 6),
         "cadence": ("step_freq_walk_median", "n_walk", 5, 2),
         "tremor": ("tremor_frac_of_rest", "n_rest", 5, 2)}
DEMO_METRIC = {"enmo": "enmo_hourly_median_mean_g", "cadence": "walking_cadence_hourly_median_mean_hz",
               "tremor": "rest_tremor_window_fraction"}


def metric_value(g, day_ok, name):
    """(ค่า, จำนวน window ที่ใช้, เหตุผลที่ใช้ไม่ได้) ตาม aggregate_daily() ของเดโม"""
    col, den, min_min, min_hours = RULES[name]
    good = g[g[den].gt(0) & g[col].notna()]
    windows = int(good[den].sum())
    reasons = []
    if not day_ok:
        reasons.append("day_coverage_below_minimum")
    if not len(good):
        reasons.append("no_eligible_windows" if g[den].sum() == 0 else "metric_values_missing")
    if windows * WINDOW_MIN < min_min or len(good) < min_hours:
        reasons.append("metric_coverage_below_minimum")
    value = None
    if not reasons:
        value = (good.n_tremor.sum() / good.n_rest.sum() if name == "tremor"
                 else np.average(good[col], weights=good[den]))
    return value, windows, "|".join(reasons), int(good.n_tremor.sum()) if name == "tremor" else None


def daily_rows(ds, sid):
    f = pd.read_csv(REPO / "data" / "features" / ds / f"{sid}.csv")
    t = pq.read_table(REPO / "data" / "clean" / ds / f"{sid}.parquet",
                      columns=["time_s", "valid", "reason", "day"]).to_pandas()
    t["hod"] = (t.time_s % 86400) / 3600
    rows, windows = [], []
    for day, raw in f.groupby("day"):
        g = raw[raw.hour.ge(CLOCK[0]) & raw.hour.lt(CLOCK[1]) & raw.valid_min.ge(MIN_HOUR_MIN)]
        day_ok = len(g) >= MIN_DAY_HOURS and g.valid_min.sum() >= MIN_DAY_MIN
        (enmo, n_win, r_enmo, _), (cad, n_walk, r_cad, _), (tremor, n_rest, r_tremor, n_tremor) = (
            metric_value(g, day_ok, m) for m in RULES)
        reasons = {k: v for k, v in (("enmo", r_enmo), ("cadence", r_cad), ("tremor", r_tremor)) if v}
        windows.append((int(day), n_win, n_walk, n_rest, n_tremor, round(float(g.valid_min.sum()), 2), len(g), reasons))

        w = t[(t.day == day) & (t.hod >= CLOCK[0]) & (t.hod < CLOCK[1])]
        wear = w.valid.sum() / FS / 3600
        nonwear = (~w.valid & w.reason.astype(str).str.contains("nonwear")).sum() / FS / 3600
        nosig = 12 - len(w) / FS / 3600 + nonwear
        lowq = 12 - wear - nosig
        qc = lambda v: "observed" if v is not None else "insufficient_coverage"
        num = lambda v, n: None if v is None else round(float(v), n)
        rows.append((int(day), num(enmo, 5), num(cad, 4), num(tremor, 5), num(wear, 2), num(max(nosig, 0), 2),
                     num(max(lowq, 0), 2), qc(enmo), qc(cad), qc(tremor)))
    return rows, windows


def check(demo_csv, ds, sid):
    """เทียบกับ daily.csv ของเดโม: วันต้องตรง ค่าต้องตรง (ปัดเศษ) และ None ตรงกับ insufficient_data"""
    d = pd.read_csv(demo_csv)
    d = d[d.subject_id == sid]
    bad = 0
    rows, windows = daily_rows(ds, sid)
    if sorted(d.day.unique()) != [r[0] for r in rows]:
        print(f"{sid}: วันไม่ตรง เดโม={sorted(d.day.unique())}")
        bad += 1
    for day, n_win, n_walk, n_rest, n_tremor, minutes, hours, reasons in windows:
        for name, n in (("enmo", n_win), ("cadence", n_walk), ("tremor", n_rest)):
            r = d[(d.day == day) & (d.metric == DEMO_METRIC[name])].iloc[0]
            theirs = (int(r.metric_windows), "" if pd.isna(r.reasons) else r.reasons,
                      round(float(r.day_valid_minutes), 2), int(r.day_eligible_hours))
            if (n, reasons.get(name, ""), minutes, hours) != theirs:
                print(f"{sid} day {day} {name}: เรา={(n, reasons.get(name, ''), minutes, hours)} เดโม={theirs}")
                bad += 1
    for day, enmo, cad, tremor, *_ in rows:
        for name, ours, digits in [("enmo", enmo, 5), ("cadence", cad, 4), ("tremor", tremor, 5)]:
            r = d[(d.day == day) & (d.metric == DEMO_METRIC[name])].iloc[0]
            theirs = None if r.status != "ok" else round(float(r.value), digits)
            if ours != theirs:
                print(f"{sid} day {day} {name}: เรา={ours} เดโม={theirs}")
                bad += 1
    print(f"{sid}: {'ตรงกับเดโมทุกค่า' if not bad else f'ไม่ตรง {bad} จุด'}")


if __name__ == "__main__":
    args = sys.argv[1:]
    demo = args[1] if args[:1] == ["--check"] else None
    args = args[2:] if demo else args
    for arg in args:
        ds, sid = arg.split("/")
        if demo:
            check(demo, ds, sid)
            continue
        rows, windows = daily_rows(ds, sid)
        print(f"    # {sid}: วัน {rows[0][0]}-{rows[-1][0]} ของต้นฉบับ (= day ของ API เดโม)")
        for i, (day, *vals) in enumerate(rows):
            print(f'        ("<วันที่ {i + 1}>", {day}, ' + ", ".join(repr(v) for v in vals) + "),")
        print(f"    # DAILY_WINDOWS {sid}: n_windows, n_walk, n_rest, n_tremor, day_valid_minutes, day_eligible_hours, reasons")
        for i, (day, *vals) in enumerate(windows):
            print(f'        ("<วันที่ {i + 1}>", ' + ", ".join(repr(v) for v in vals) + "),")

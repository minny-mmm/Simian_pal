"""คัดคนปกติจาก NHANES 2013–2014 (PAX80_H) มาเทียบกับผู้ป่วย COPS -> metadata/selected_seqn.csv

    python tools/select_nhanes.py            # คัด + เขียนไฟล์ (seed คงที่ = ได้ผลเดิม)
    python tools/select_nhanes.py --check    # แสดงจำนวนที่ผ่านแต่ละเกณฑ์ ไม่เขียนไฟล์
    python tools/make_splits.py              # แล้วแบ่ง train / valid / test

ไฟล์ที่ใช้ (data/raw/nhanes/2013-2014/):
    demographics/DEMO_H.xpt     อายุ (RIDAGEYR), เพศ (RIAGENDR)
    pax_summary/PAXHD_H.xpt     สถานะข้อมูล, ข้อมือ, การวางเครื่อง
    pax_summary/PAXDAY_H.xpt    เวลาใส่นาฬิการายวัน
    health_history/RXQ_RX_H.xpt ยา, MCQ_H.xpt โรคประจำตัว

เกณฑ์
1. มีข้อมูล accelerometer (PAXSTS = 1) ใส่ข้อมือข้างไม่ถนัด (PAXHAND = 1) วางด้านหลังข้อมือ (PAXORENT = 1)
2. วันที่ใช้ได้ >= MIN_VALID_DAYS (วันที่ตื่นและใส่ PAXWWMD >= 600 นาที ตรงกับ valid_days ใน pilot_sample.csv)
3. อายุอยู่ในช่วงอายุของ COPS (use_for_main_model == yes)
4. ไม่กินยากลุ่มพาร์กินสัน / dopaminergic (PD_DRUGS) และไม่เคยเป็นโรคหลอดเลือดสมอง (MCQ160F = 1)
5. สุ่มให้สัดส่วนเพศ x ช่วงอายุ 5 ปี ตรงกับ COPS รวม N_TOTAL คน
   คนใน pilot ที่ผ่านเกณฑ์ถูกเลือกก่อน (มีไฟล์ดิบในเครื่องแล้ว)
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NH = ROOT / "data/raw/nhanes/2013-2014"
SEED = 0
N_TOTAL = 150
MIN_VALID_DAYS = 4
MIN_WAKE_WEAR_MIN = 600
AGE_BAND = 5
PD_DRUGS = ["CARBIDOPA", "LEVODOPA", "PRAMIPEXOLE", "ROPINIROLE", "ROTIGOTINE", "RASAGILINE",
            "SELEGILINE", "SAFINAMIDE", "ENTACAPONE", "TOLCAPONE", "AMANTADINE", "BENZTROPINE",
            "TRIHEXYPHENIDYL", "APOMORPHINE"]


def xpt(path):
    df = pd.read_sas(path, format="xport", encoding="latin1")
    df["SEQN"] = df["SEQN"].astype(int)
    return df


def to_num(s):
    """คอลัมน์รหัสที่ pandas อ่านเป็น bytes/str -> ตัวเลข"""
    return pd.to_numeric(s.astype(str).str.strip(), errors="coerce")


def band(age):
    return (age // AGE_BAND) * AGE_BAND


def cops_reference():
    s = pd.read_csv(ROOT / "reports/cops_visit_summary.csv")
    d = pd.read_csv(ROOT / "data/raw/cops/Demographics.csv", sep=";")
    ref = d[d["ID"].isin(s.loc[s["use_for_main_model"] == "yes", "subject_id"])]
    return pd.DataFrame({"age": ref["Age"], "sex": ref["Sex"].str[0].str.upper()})


def eligible(ref, log):
    demo = xpt(NH / "demographics/DEMO_H.xpt")
    df = pd.DataFrame({"seqn": demo["SEQN"], "age": demo["RIDAGEYR"].astype(int),
                       "sex": demo["RIAGENDR"].map({1: "M", 2: "F"})})
    log("NHANES 2013-2014 ทั้งหมด", df)

    hd = xpt(NH / "pax_summary/PAXHD_H.xpt")
    ok = hd[(hd["PAXSTS"] == 1) & (to_num(hd["PAXHAND"]) == 1) & (to_num(hd["PAXORENT"]) == 1)]
    df = df[df["seqn"].isin(ok["SEQN"])]
    log("มี accelerometer, ข้อมือข้างไม่ถนัด, วางด้านหลังข้อมือ", df)

    day = xpt(NH / "pax_summary/PAXDAY_H.xpt")
    vd = day[day["PAXWWMD"] >= MIN_WAKE_WEAR_MIN].groupby("SEQN").size()
    df = df.assign(valid_days=df["seqn"].map(vd).fillna(0).astype(int))
    df = df[df["valid_days"] >= MIN_VALID_DAYS]
    log(f"วันที่ใช้ได้ >= {MIN_VALID_DAYS} (ตื่นและใส่ >= {MIN_WAKE_WEAR_MIN // 60} ชม.)", df)

    lo, hi = int(ref["age"].min()), int(ref["age"].max())
    df = df[df["age"].between(lo, hi)]
    log(f"อายุ {lo}-{hi} ปี (ช่วงเดียวกับ COPS)", df)

    rx = xpt(NH / "health_history/RXQ_RX_H.xpt")
    on_pd = rx.loc[rx["RXDDRUG"].fillna("").str.upper().str.contains("|".join(PD_DRUGS)), "SEQN"]
    df = df[~df["seqn"].isin(on_pd)]
    log("ไม่กินยากลุ่มพาร์กินสัน", df)

    mcq = xpt(NH / "health_history/MCQ_H.xpt")
    stroke = mcq.loc[mcq["MCQ160F"] == 1, "SEQN"]
    df = df[~df["seqn"].isin(stroke)]
    log("ไม่เคยเป็นโรคหลอดเลือดสมอง", df)
    return df.assign(age_band=band(df["age"]))


def quotas(ref, n_total):
    """จำนวนที่ต้องการต่อ เพศ x ช่วงอายุ ตามสัดส่วนของ COPS (ปัดแบบ largest remainder)"""
    share = ref.assign(age_band=band(ref["age"])).groupby(["sex", "age_band"]).size() / len(ref)
    raw = share * n_total
    q = np.floor(raw).astype(int)
    q[(raw - q).sort_values(ascending=False).index[: n_total - q.sum()]] += 1
    return q


def sample(pool, q, pilot, seed=SEED):
    rng = np.random.default_rng(seed)
    picked = []
    for (sex, ab), n in q.items():
        cell = pool[(pool["sex"] == sex) & (pool["age_band"] == ab)]
        first = cell[cell["seqn"].isin(pilot)]
        rest = cell[~cell["seqn"].isin(pilot)]
        n_rest = max(0, n - len(first))
        if n_rest > len(rest):
            raise SystemExit(f"{sex} อายุ {ab}-{ab + AGE_BAND - 1}: ต้องการ {n} คน มีแค่ {len(cell)}")
        picked += [first.head(n), rest.iloc[rng.permutation(len(rest))[:n_rest]]]
    return pd.concat(picked).sort_values("seqn")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="แสดงผล ไม่เขียนไฟล์")
    args = ap.parse_args()

    ref = cops_reference()
    log = lambda step, df: print(f"{len(df):6d}  {step}")
    pool = eligible(ref, log)
    pilot = pd.read_csv(ROOT / "tools/pilot_sample.csv")
    pilot = set(pilot.loc[pilot["dataset"] == "NHANES", "id"].astype(int))
    print(f"pilot ที่ผ่านเกณฑ์: {sorted(pilot & set(pool['seqn']))} | ไม่ผ่าน: {sorted(pilot - set(pool['seqn']))}")

    q = quotas(ref, N_TOTAL)
    sel = sample(pool, q, pilot)
    table = pd.DataFrame({"COPS": ref.assign(age_band=band(ref["age"])).groupby(["sex", "age_band"]).size(),
                          "เป้า": q, "มีในเกณฑ์": pool.groupby(["sex", "age_band"]).size(),
                          "เลือก": sel.groupby(["sex", "age_band"]).size()}).fillna(0).astype(int)
    print(table.to_string())
    print(f"เลือก {len(sel)} คน | อายุเฉลี่ย NHANES {sel['age'].mean():.1f} vs COPS {ref['age'].mean():.1f} | "
          f"ชาย {(sel['sex'] == 'M').mean():.0%} vs {(ref['sex'] == 'M').mean():.0%}")
    if not args.check:
        out = sel[["seqn", "age", "sex", "valid_days", "age_band"]].assign(pilot=sel["seqn"].isin(pilot))
        out.to_csv(ROOT / "metadata/selected_seqn.csv", index=False)
        print("เขียน metadata/selected_seqn.csv (รัน tools/make_splits.py ต่อเพื่อแบ่ง train / valid / test)")


if __name__ == "__main__":
    main()

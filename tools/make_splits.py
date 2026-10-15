"""แบ่งคนเป็น train / valid / test แล้วเขียน metadata/cops_splits.csv และ metadata/nhanes_splits.csv

    python tools/make_splits.py            # แบ่งใหม่ (seed คงที่ = ได้ผลเดิมทุกครั้ง)
    python tools/make_splits.py --check    # แสดงสัดส่วนในแต่ละกลุ่ม ไม่เขียนไฟล์

หลักการ
- แบ่งตามคน (ข้อมูลของคนเดียวกันอยู่กลุ่มเดียว) สัดส่วน 70 / 15 / 15
- COPS: คนที่ use_for_main_model == yes ใน reports/cops_visit_summary.csv
  stratify ตามชนิด PD x ระดับ H&Y (<= 2 / >= 2.5) แล้วลอง seed 0..N_SEEDS-1
  เลือก seed ที่สัดส่วนเพศ, ชนิด, H&Y, DBS และอายุเฉลี่ยของ 3 กลุ่มใกล้กันที่สุด
  use_wrist = ข้อมือข้างไม่ถนัด (ตรงกับ NHANES ที่ใส่ข้อมือข้างไม่ถนัด)
- NHANES: ทุก seqn ใน metadata/nhanes_splits.csv (สร้างด้วย tools/select_nhanes.py)
  กระจายเพศ x ช่วงอายุ 10 ปี ถ้ามีคอลัมน์ sex / age และมีคนพอ
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]
N_SEEDS = 500
FRAC_VALID, FRAC_TEST = 0.15, 0.15
MIN_STRATIFIED = 20      # คนน้อยกว่านี้แบ่งแบบสุ่มธรรมดา (stratify ไม่ได้)


def assign(ids, strata=None, seed=0):
    """คืน Series split ตาม ids: test ก่อน แล้วแบ่งที่เหลือเป็น valid / train"""
    ids = pd.Series(ids).reset_index(drop=True)
    n = len(ids)
    n_test, n_valid = round(n * FRAC_TEST), round(n * FRAC_VALID)
    strat = strata.reset_index(drop=True) if strata is not None and n >= MIN_STRATIFIED else None
    rest, test = train_test_split(ids.index, test_size=n_test, random_state=seed,
                                  stratify=None if strat is None else strat)
    train, valid = train_test_split(rest, test_size=n_valid, random_state=seed,
                                    stratify=None if strat is None else strat[rest])
    split = pd.Series("train", index=ids.index)
    split[valid], split[test] = "valid", "test"
    return pd.Series(split.values, index=ids.values)


def imbalance(df):
    """ผลรวมความต่างสูงสุดของสัดส่วนแต่ละค่าระหว่างกลุ่ม + ความต่างอายุเฉลี่ย (/10 ปี)"""
    score = 0.0
    for c in ["Sex", "PD_Subtype", "hy_group", "DBS"]:
        prop = pd.crosstab(df[c], df["split"], normalize="columns")
        score += float((prop.max(1) - prop.min(1)).max())
    age = df.groupby("split")["Age"].mean()
    return score + float(age.max() - age.min()) / 10


def cops_splits():
    summary = pd.read_csv(ROOT / "reports/cops_visit_summary.csv")
    demo = pd.read_csv(ROOT / "data/raw/cops/Demographics.csv", sep=";").rename(columns={"ID": "subject_id"})
    df = summary[summary["use_for_main_model"] == "yes"].merge(demo, on="subject_id", how="left")
    if df["Handedness"].isna().any():
        raise SystemExit(f"ไม่มีใน Demographics.csv: {df.loc[df.Handedness.isna(), 'subject_id'].tolist()}")
    df["use_wrist"] = df["Handedness"].str.lower().map({"right": "left", "left": "right"})
    df["hy_group"] = np.where(df["PD_HoehnAndYahr"] <= 2, "hy<=2", "hy>=2.5")
    strata = df["PD_Subtype"] + "_" + df["hy_group"]
    best = min(range(N_SEEDS), key=lambda sd: imbalance(df.assign(split=assign(df["subject_id"], strata, sd).values)))
    df["split"] = assign(df["subject_id"], strata, best).values
    print(f"COPS: seed {best} (imbalance {imbalance(df):.3f})")
    pilot_path = ROOT / "tools/pilot_sample.csv"                # ไม่อยู่ใน git: ไม่มีไฟล์ = pilot เป็น False ทุกคน
    pilot = pd.read_csv(pilot_path) if pilot_path.exists() else pd.DataFrame(columns=["dataset", "id"])
    df["pilot"] = df["subject_id"].isin(pilot.loc[pilot["dataset"] == "COPS", "id"])   # อยู่ใน pilot_sample.csv
    return df.rename(columns={"Sex": "sex", "Age": "age", "Handedness": "handedness",
                              "PD_HoehnAndYahr": "hoehn_yahr", "PD_Subtype": "pd_subtype", "DBS": "dbs"})[
        ["subject_id", "age", "sex", "use_wrist", "handedness", "hoehn_yahr", "pd_subtype", "dbs",
         "outpatient_days", "pilot", "split"]]


def nhanes_splits():
    path = ROOT / "metadata/nhanes_splits.csv"
    df = pd.read_csv(path, dtype={"seqn": str}).drop(columns="split", errors="ignore")
    strata = None
    if {"sex", "age"} <= set(df.columns):
        strata = df["sex"].str.upper() + "_" + (df["age"] // 10 * 10).astype(str)
    df["split"] = assign(df["seqn"], strata).values
    return df


def show(df, cols):
    for c in cols:
        print(pd.crosstab(df[c], df["split"]).reindex(columns=["train", "valid", "test"]).to_string(), "\n")
    if "age" in df:
        print(df.groupby("split")["age"].agg(["count", "mean", "min", "max"]).round(1).to_string(), "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="แสดงผลแบ่ง ไม่เขียนไฟล์")
    args = ap.parse_args()

    cops = cops_splits()
    nh = nhanes_splits()
    print(f"COPS {len(cops)} คน: {cops['split'].value_counts().to_dict()}")
    show(cops, ["sex", "hoehn_yahr", "pd_subtype", "dbs", "use_wrist"])
    print(f"NHANES {len(nh)} คน: {nh['split'].value_counts().to_dict()}")
    show(nh.assign(age_group=nh["age"] // 10 * 10), ["sex", "age_group"])
    if not args.check:
        cops.to_csv(ROOT / "metadata/cops_splits.csv", index=False)
        nh.to_csv(ROOT / "metadata/nhanes_splits.csv", index=False)
        print("เขียน metadata/cops_splits.csv, metadata/nhanes_splits.csv")


if __name__ == "__main__":
    main()

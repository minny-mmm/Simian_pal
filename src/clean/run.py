"""clean ข้อมูล accelerometer ทีละคน -> data/clean/<dataset>/<subject_id>.parquet + reports/qc_summary.csv

    python -m src.clean.run                         # ทุกคนในรายชื่อที่มีไฟล์ดิบในเครื่อง (COPS ก่อน NHANES)
    python -m src.clean.run --ids COPS-8 NHANES-74132
    python -m src.clean.run --force                 # ทำใหม่แม้มีผลแล้ว
    python -m src.clean.run --summary-only          # รวม qc_summary.csv จากผลที่มีอยู่

รายชื่อ: COPS = use_for_main_model == yes ใน cops_visit_summary.csv (ข้อมือ / split จาก cops_splits.csv)
         NHANES = nhanes_splits.csv
คนที่มีผลแล้ว (.parquet + .qc.json) จะถูกข้าม
"""
import argparse
import json
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from src import storage
from src.clean import steps
from src.clean.steps import BIT
from src.ingestion import READERS

ROOT = Path(__file__).resolve().parents[2]
REMOVE = ("missing", "clip", "abnormal", "ism", "nonwear", "sleep", "visit")   # ขั้น 10


def load_config(path=ROOT / "config" / "config.yaml"):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- รายชื่อ

def load_subjects(config):
    c = config["clean"]["subjects"]
    summary = pd.read_csv(ROOT / c["cops_summary"], dtype=str)
    splits = pd.read_csv(ROOT / c["cops_splits"], dtype=str)
    cops = summary[summary["use_for_main_model"].str.lower() == "yes"].merge(
        splits, on="subject_id", how="left")
    missing = cops[cops["use_wrist"].isna()]["subject_id"].tolist()
    if missing:
        print(f"คำเตือน: {len(missing)} คนใน COPS ไม่มีใน {c['cops_splits']} (ข้าม): {missing[:8]}")
    cops = cops.dropna(subset=["use_wrist"])
    nh = pd.read_csv(ROOT / c["nhanes_selected"], dtype=str)
    rows = [dict(dataset="cops", subject_id=r.subject_id, wrist=r.use_wrist.lower(), split=r.split)
            for r in cops.itertuples()]
    rows += [dict(dataset="nhanes", subject_id=f"NHANES-{r.seqn}", wrist=None, split=r.split)
             for r in nh.itertuples()]
    return rows


def make_reader(config, dataset):
    return READERS[dataset](config["datasets"][dataset]["raw_dir"], ROOT)


def out_paths(config, subj):
    d = ROOT / config["clean"]["out_dir"] / subj["dataset"]
    return d / f"{subj['subject_id']}.parquet", d / f"{subj['subject_id']}.qc.json"


def resolve_noise_target(config, subjects):
    """target_mg: auto = มัธยฐาน noise floor หลังปรับของ COPS กลุ่ม train ที่ clean แล้ว
    (กลุ่ม train อ่านจาก cops_splits.csv ปัจจุบัน ไม่ใช่ค่าที่บันทึกไว้ตอน clean)"""
    target = config["clean"]["harmonize"]["add_noise"]["target_mg"]
    if target != "auto":
        return float(target), "config"
    train = {s["subject_id"] for s in subjects if s["dataset"] == "cops" and s["split"] == "train"}
    vals = []
    for p in sorted((ROOT / config["clean"]["out_dir"] / "cops").glob("*.qc.json")):
        q = json.loads(p.read_text())
        if q["subject_id"] in train and q.get("noise_floor_after_mg") is not None \
                and np.isfinite(q["noise_floor_after_mg"]):
            vals.append(q["noise_floor_after_mg"])
    if not vals:
        return None, "no_cops_train"
    return float(np.median(vals)), f"median_cops_train_n={len(vals)}"


# ---------------------------------------------------------------- clean 1 คน

def clean_subject(config, subj, reader, noise_target, log=print):
    c = config["clean"]
    ds_cfg = config["datasets"][subj["dataset"]]
    fs_in, fs = ds_cfg["sampling_rate_hz"], c["resample"]["fs_out"]
    sid = subj["subject_id"]
    t0 = time.time()
    qc = dict(subject_id=sid, dataset=subj["dataset"], split=subj["split"], wrist=subj["wrist"],
              fs_native=fs_in, fs_out=fs)

    # ขั้น 1 + 2 + 4: อ่านทีละไฟล์ -> grid สม่ำเสมอที่ความถี่เดิม -> resample (ไม่เก็บข้อมูลความถี่เดิมทั้งก้อน)
    tf = steps.TimeFixer(fs_in, c["time"]["max_interp_gap_s"])
    rs = steps.Resampler(fs_in, fs, c["resample"]["chunk_s"], c["resample"]["context_s"],
                         c["resample"]["min_run_s"])
    n_files = 0
    for t, v in reader.iter_hours(sid, subj["wrist"]):
        n_files += 1
        for start, vals in tf.push(t, v):
            rs.push(start, vals)
    hour = int(3600 * fs)
    xyz = rs.assemble(align=hour)
    qc.update(n_files=n_files, n_unsorted_files=tf.n_unsorted, n_duplicates=tf.n_duplicates,
              n_interpolated=tf.n_interpolated, n_long_gaps=tf.n_long_gaps,
              n_short_runs_dropped=rs.n_short_runs)
    if xyz is None:
        return None, {**qc, "status": "no_data"}
    log(f"  อ่าน {n_files} ไฟล์ -> {len(xyz) / fs / 3600:.1f} ชม. ที่ {fs} Hz ({time.time() - t0:.0f} วิ)")

    n = len(xyz)
    flags = np.zeros(n, np.uint16)
    missing = np.isnan(xyz[:, 0])
    flags[missing] |= BIT["missing"]

    # ขั้น 3: calibrate
    # หาค่าจากข้อมูล 50 Hz แล้วปรับหลัง resample: ให้ผลเท่ากับปรับก่อน resample
    # เพราะการปรับ (offset/scale) และ resample_poly (padtype=line) เป็น linear ทั้งคู่
    # ไม่ใช้ช่วงที่แหล่งข้อมูลบอกว่าผิดปกติ (เช่น Logs ของ NHANES) ในการหาค่า
    intervals = reader.flag_intervals(sid)
    cal = steps.estimate_calibration(xyz, fs, c["calibration"],
                                     exclude=steps.intervals_mask(n, fs, intervals))
    steps.apply_calibration(xyz, cal)
    qc.update({k: v for k, v in cal.items() if k not in ("cal_offset", "cal_scale")})
    qc.update({f"cal_offset_{a}": o for a, o in zip("xyz", cal["cal_offset"])})
    qc.update({f"cal_scale_{a}": s for a, s in zip("xyz", cal["cal_scale"])})

    # ขั้น 5: clip
    flags[steps.clip_and_flag(xyz, c["clip"])] |= BIT["clip"]

    # ขั้น 6: สัญญาณผิดปกติ (กฎเดียวกัน + ช่วงที่แหล่งข้อมูลบอกมา)
    abn = steps.abnormal_mask(xyz, fs, c["abnormal"], intervals)
    grav = steps.impossible_gravity_mask(xyz, fs, c["abnormal"])
    qc["pct_impossible_gravity"] = round(100 * float(np.sum(grav & ~missing)) / max(int((~missing).sum()), 1), 2)
    flags[abn | grav] |= BIT["abnormal"]
    del abn, grav

    # ขั้น 7: ช่วงนิ่ง (ISM) แล้วปรับระดับ noise
    ism = steps.ism_mask(xyz, fs, c["ism"])
    flags[ism] |= BIT["ism"]
    h = c["harmonize"]

    def measure():
        nw = steps.nonwear_mask(xyz, fs, c["nonwear"])
        nf, nwin = steps.noise_floor_mg(xyz, fs, h["noise_window_s"], nw & ~missing, h["min_noise_windows"])
        if np.isfinite(nf):
            return nf, nwin, "nonwear"
        nf, nwin = steps.noise_floor_mg(xyz, fs, h["noise_window_s"], ism & ~missing, h["min_noise_windows"])
        return nf, nwin, "ism"

    nf_before, _, src = measure()
    added = 0.0
    if subj["dataset"] in h["add_noise"]["datasets"]:
        if noise_target is None:
            raise RuntimeError("ยังไม่มี noise target: clean COPS กลุ่ม train ก่อน หรือใส่ target_mg ใน config")
        # การปัดเป็นขั้น (ทำหลังเติม noise) เพิ่ม noise อีก step/sqrt(12) จึงหักออกจากที่ต้องเติม
        q_mg = (h["quantize"]["step_g"] * 1000 / np.sqrt(12)
                if subj["dataset"] in h["quantize"]["datasets"] else 0.0)
        need = noise_target ** 2 - nf_before ** 2 - q_mg ** 2
        if np.isfinite(nf_before) and need > 0:
            added = float(np.sqrt(need))
            seed = h["add_noise"]["seed"] + zlib.crc32(sid.encode())
            steps.add_noise(xyz, added / 1000, seed)
            np.clip(xyz, -c["clip"]["limit_g"], c["clip"]["limit_g"], out=xyz)
    if subj["dataset"] in h["quantize"]["datasets"]:   # หลังเติม noise
        steps.quantize(xyz, h["quantize"]["step_g"])
    nf_after, nf_windows, src_after = measure()
    qc.update(noise_floor_before_mg=nf_before, noise_floor_after_mg=nf_after, noise_added_mg=added,
              noise_target_mg=noise_target, noise_source=src_after, noise_windows=nf_windows)

    # ขั้น 8: ถอดนาฬิกา
    nonwear = steps.nonwear_mask(xyz, fs, c["nonwear"])
    flags[nonwear] |= BIT["nonwear"]

    # ขั้น 9: นอน + เทียบกับ diary (ถ้ามี)
    sleep, nights = steps.hdcza(xyz, fs, c["sleep"])
    flags[sleep] |= BIT["sleep"]
    labels = reader.hour_labels(sid)
    n_hours = n // hour
    visit_h = np.full(n_hours, c["default_visit"] if labels is None else "unknown", dtype=object)
    if labels is not None:
        lab = labels[(labels["hour"] >= 0) & (labels["hour"] < n_hours)]
        visit_h[lab["hour"].to_numpy()] = lab["visit"].to_numpy()
        diary = np.full(n_hours, np.nan)
        diary[lab["hour"].to_numpy()] = lab["diary_sleep"].to_numpy()
        qc.update(steps.compare_hourly(sleep, ~missing, diary, fs))
    qc.update(sleep_nights=len(nights),
              sleep_hours_per_night=round(float(np.mean([x["hours"] for x in nights])), 2) if nights else np.nan)

    # visit: ใช้เฉพาะชั่วโมงที่ visit อยู่ใน include_visits
    bad_visit = ~np.isin(visit_h, c["days"]["include_visits"])
    flags[np.repeat(bad_visit, hour)] |= BIT["visit"]

    # ขั้น 10: ช่วงที่ใช้ได้
    remove = np.uint16(sum(int(BIT[f]) for f in REMOVE))
    valid = (flags & remove) == 0

    # ขั้น 11: ตัดสินวัน
    dh = steps.day_hours(valid, fs)
    good_day = dh >= c["days"]["min_valid_hours"]
    per_day = int(86400 * fs)
    short = valid & ~np.repeat(good_day, per_day)[:n]
    flags[short] |= BIT["short_day"]
    valid &= ~short
    n_valid_days = int(good_day.sum())
    included = n_valid_days >= c["days"]["min_valid_days"]
    if not included:
        flags[valid] |= BIT["few_days"]
        valid[:] = False

    # QC
    rec = ~missing
    n_rec = max(int(rec.sum()), 1)
    first, last = int(np.argmax(rec)), int(n - 1 - np.argmax(rec[::-1]))
    pct = lambda f: round(100 * float(np.sum(((flags & BIT[f]) > 0) & rec)) / n_rec, 2)
    awake = rec & ~sleep
    rec_h, vis_mode = steps.day_hours(rec, fs), []
    for d in range(len(dh)):
        vh = visit_h[d * 24:(d + 1) * 24]
        vis_mode.append(pd.Series(vh).mode().iat[0] if len(vh) else None)
    qc.update(
        hours_total=round((last - first + 1) / fs / 3600, 2),
        hours_recorded=round(n_rec / fs / 3600, 2),
        hours_missing=round((last - first + 1 - n_rec) / fs / 3600, 2),
        pct_clip=pct("clip"), pct_abnormal=pct("abnormal"), pct_ism=pct("ism"),
        pct_nonwear=pct("nonwear"), pct_sleep=pct("sleep"), pct_visit=pct("visit"),
        pct_awake_ism=round(100 * float(np.sum(ism & awake)) / max(int(awake.sum()), 1), 2),
        hours_valid=round(float(valid.sum()) / fs / 3600, 2),
        n_days=int(np.sum(rec_h > 0)), valid_days=n_valid_days, included=bool(included),
        status="ok", runtime_s=round(time.time() - t0, 1),
    )
    qc["days"] = [dict(day=d, recorded_h=round(float(r), 2), valid_h=round(float(v), 2),
                       visit=vis_mode[d], valid=bool(g))
                  for d, (r, v, g) in enumerate(zip(rec_h, dh, good_day))]
    qc["nights"] = nights
    return dict(xyz=xyz, flags=flags, valid=valid, visit_h=visit_h, fs=fs, first=first, last=last), qc


# ---------------------------------------------------------------- เขียนผล

def dict_array(codes, names):
    """codes (int) + รายชื่อ -> pyarrow dictionary array (None ใน names = null)"""
    names = list(names)
    mask = np.array([nm is None for nm in names])[codes]
    dictionary = pa.array([nm if nm is not None else "" for nm in names], pa.string())
    return pa.DictionaryArray.from_arrays(pa.array(codes.astype(np.int32), mask=mask), dictionary)


def write_parquet(path, res):
    """1 row group ต่อวัน เขียนเฉพาะตั้งแต่ตัวอย่างแรกถึงตัวอย่างสุดท้ายที่มีข้อมูล"""
    fs, xyz, flags, valid = res["fs"], res["xyz"], res["flags"], res["valid"]
    per_day, hour = int(86400 * fs), int(3600 * fs)
    visit_names, visit_codes_h = np.unique(res["visit_h"].astype(str), return_inverse=True)
    schema = pa.schema([
        ("time_s", pa.float64()), ("x", pa.float32()), ("y", pa.float32()), ("z", pa.float32()),
        ("valid", pa.bool_()), ("reason", pa.dictionary(pa.int32(), pa.string())),
        ("day", pa.int16()), ("visit", pa.dictionary(pa.int32(), pa.string())),
    ])
    tmp = path.with_suffix(".parquet.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(tmp, schema, compression="zstd") as w:
        for a in range(res["first"] // per_day * per_day, res["last"] + 1, per_day):
            lo, hi = max(a, res["first"]), min(a + per_day, res["last"] + 1)
            idx = np.arange(lo, hi)
            uniq, inv = np.unique(flags[lo:hi], return_inverse=True)
            w.write_table(pa.table([
                pa.array(idx / fs), pa.array(xyz[lo:hi, 0]), pa.array(xyz[lo:hi, 1]), pa.array(xyz[lo:hi, 2]),
                pa.array(valid[lo:hi]), dict_array(inv, [steps.flag_names(u) for u in uniq]),
                pa.array(np.full(hi - lo, a // per_day, np.int16)),
                dict_array(visit_codes_h[idx // hour], visit_names),
            ], schema=schema))
    tmp.replace(path)


def to_json(obj):
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    return obj


def write_summary(config):
    rows = []
    for p in sorted((ROOT / config["clean"]["out_dir"]).glob("*/*.qc.json")):
        q = json.loads(p.read_text())
        q.pop("days", None)
        q.pop("nights", None)
        rows.append(q)
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values(["dataset", "subject_id"])
    path = ROOT / config["clean"]["qc_summary"]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def main():
    ap = argparse.ArgumentParser(description="clean ข้อมูล accelerometer ทีละคน")
    ap.add_argument("--ids", nargs="+", help="subject_id ที่จะทำ (ค่าเริ่มต้น = ทุกคนที่มีไฟล์ดิบ)")
    ap.add_argument("--force", action="store_true", help="ทำใหม่แม้มีผลแล้ว")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--config", default=ROOT / "config" / "config.yaml")
    ap.add_argument("--upload", action="store_true",
                    help="อัปโหลดผล clean (.parquet + .qc.json) ขึ้น storage.s3_root หลังทำแต่ละคน")
    args = ap.parse_args()
    config = load_config(args.config)
    storage.configure(config.get("storage"))
    s3_root = storage.s3_root(config) if args.upload else None

    def push(*paths):
        for p in paths:
            if p.exists():
                print(f"  S3 {storage.upload(p, storage.mirror(p, ROOT, s3_root))}: {p.relative_to(ROOT)}")

    if not args.summary_only:
        subjects = all_subjects = load_subjects(config)
        if args.ids:
            unknown = set(args.ids) - {s["subject_id"] for s in subjects}
            if unknown:
                raise SystemExit(f"ไม่อยู่ในรายชื่อ: {sorted(unknown)}")
            subjects = [s for s in subjects if s["subject_id"] in args.ids]
        readers = {ds: make_reader(config, ds) for ds in {s["dataset"] for s in subjects}}
        # COPS ก่อน เพราะ NHANES ใช้ noise floor ของ COPS เป็นเป้า
        for subj in sorted(subjects, key=lambda s: s["dataset"] != "cops"):
            pq_path, qc_path = out_paths(config, subj)
            reader = readers[subj["dataset"]]
            if not reader.has_raw(subj["subject_id"]):
                continue
            if pq_path.exists() and qc_path.exists() and not args.force:
                print(f"{subj['subject_id']}: มีผลแล้ว ข้าม")
                if s3_root:
                    push(pq_path, qc_path)
                continue
            print(f"{subj['subject_id']}: เริ่ม")
            target, target_src = (resolve_noise_target(config, all_subjects)
                                  if subj["dataset"] in config["clean"]["harmonize"]["add_noise"]["datasets"]
                                  else (None, None))
            res, qc = clean_subject(config, subj, reader, target)
            qc["noise_target_source"] = target_src
            if res is not None:
                write_parquet(pq_path, res)
                del res
            qc_path.parent.mkdir(parents=True, exist_ok=True)
            qc_path.write_text(json.dumps(to_json(qc), ensure_ascii=False, indent=1))
            print(f"{subj['subject_id']}: เสร็จ ({qc.get('runtime_s')} วิ) "
                  f"วันใช้ได้ {qc.get('valid_days')} ชม.ใช้ได้ {qc.get('hours_valid')}")
            if s3_root:
                push(pq_path, qc_path)

    df = write_summary(config)
    if df is not None:
        print(f"เขียน {config['clean']['qc_summary']}: {len(df)} คน")


if __name__ == "__main__":
    main()

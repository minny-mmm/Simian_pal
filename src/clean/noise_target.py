"""เป้า noise ต่อแกนของ NHANES = มัธยฐานต่อแกนของ noise floor หลัง clean ของ COPS กลุ่ม train

ใช้โดย tools/noise_target.py (คำนวณ / เทียบกับ config) และ src/clean/run.py (เตือนหลัง clean COPS ใหม่)
ค่าต่อคนอ่านจาก .qc.json (noise_floor_after_axes_mg) ถ้า qc เก่ายังไม่มีช่องนี้ วัดจาก parquet แทน
ด้วยฟังก์ชันและหน้ากากเดียวกับขั้น clean (src/clean/run.py: measure) จึงได้ค่าเท่ากับตอน clean
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from src.clean import steps
from src.clean.report import _flag_bits

AXES_KEY = "noise_floor_after_axes_mg"


def axes_from_parquet(path, config):
    """parquet ที่ clean แล้ว -> (noise ต่อแกน mg, noise เฉลี่ย 3 แกน mg, จำนวนหน้าต่าง, แหล่งที่วัด)
    เลือกช่วงถอดนาฬิกาก่อน ถ้าหน้าต่างไม่พอใช้ช่วง ISM (เหมือน measure ในขั้น clean)"""
    c, h = config["clean"], config["clean"]["harmonize"]
    fs = c["resample"]["fs_out"]
    win = int(h["noise_window_s"] * fs)
    t = pq.read_table(path, columns=["time_s", "x", "y", "z", "reason"])
    # parquet เริ่มที่ตัวอย่างแรกที่มีข้อมูล: เติม NaN ข้างหน้าให้หน้าต่างตรงกับตอน clean (นับจาก 00:00 วันแรก)
    pad = int(round(t["time_s"][0].as_py() * fs)) % win
    xyz = np.full((pad + len(t), 3), np.nan, np.float32)
    for i, a in enumerate("xyz"):
        xyz[pad:, i] = t[a].to_numpy()
    bits = np.zeros(len(xyz), np.uint16)
    bits[pad:] = _flag_bits(t["reason"])
    del t
    missing = (bits & steps.BIT["missing"]) > 0
    for flag in ("nonwear", "ism"):
        mask = ((bits & steps.BIT[flag]) > 0) & ~missing
        nf, nwin = steps.noise_floor_mg(xyz, fs, h["noise_window_s"], mask, h["min_noise_windows"])
        if np.isfinite(nf):
            break
    axes, _ = steps.noise_floor_axes_mg(xyz, fs, h["noise_window_s"], mask, h["min_noise_windows"])
    return axes, nf, nwin, flag


def cops_train_axes(config, subjects, root, use_parquet=True, write_qc=False):
    """noise ต่อแกนของ COPS กลุ่ม train ทุกคน -> DataFrame (subject_id, x, y, z, from)
    from = qc (มีใน .qc.json), parquet (วัดใหม่), missing (ยังไม่ได้ clean / ไม่มีไฟล์)
    write_qc = เขียนค่าที่วัดจาก parquet ลง .qc.json ที่ยังไม่มี (ครั้งหน้าไม่ต้องวัดซ้ำ)"""
    out_dir = Path(root) / config["clean"]["out_dir"] / "cops"
    rows = []
    for s in subjects:
        if s["dataset"] != "cops" or s["split"] != "train":
            continue
        sid = s["subject_id"]
        qc_path, pq_path = out_dir / f"{sid}.qc.json", out_dir / f"{sid}.parquet"
        row = dict(subject_id=sid, x=np.nan, y=np.nan, z=np.nan, src="missing")
        qc = json.loads(qc_path.read_text()) if qc_path.exists() else None
        if qc is not None and qc.get(AXES_KEY) is not None:
            row.update(zip("xyz", qc[AXES_KEY]), src="qc")
        elif qc is not None and use_parquet and pq_path.exists():
            axes, nf, _, _ = axes_from_parquet(pq_path, config)
            row.update(zip("xyz", axes), src="parquet",
                       check_mean=nf - (qc.get("noise_floor_after_mg") or np.nan))   # ควรเป็น ~0
            if write_qc and np.isfinite(axes).all():
                qc[AXES_KEY] = [float(v) for v in axes]
                qc_path.write_text(json.dumps(qc, ensure_ascii=False, indent=1))
        rows.append(row)
    return pd.DataFrame(rows)


def compare(config, df, tol_mg):
    """-> (เป้าที่คำนวณได้, เป้าใน config หรือ None ถ้าเป็น auto, ข้อความเตือน list)"""
    ok = df[df["src"] != "missing"]
    computed = np.median(ok[["x", "y", "z"]].to_numpy(float), 0) if len(ok) else np.full(3, np.nan)
    target = config["clean"]["harmonize"]["add_noise"]["target_mg"]
    configured = None if target == "auto" else np.broadcast_to(np.asarray(target, float), (3,))
    warn = []
    n_missing = int((df["src"] == "missing").sum())
    if n_missing:
        warn.append(f"COPS train {n_missing} คนยังไม่มีค่า: {df.loc[df.src == 'missing', 'subject_id'].tolist()}")
    if configured is not None and np.isfinite(computed).all():
        diff = np.abs(computed - configured)
        if (diff > tol_mg).any():
            warn.append(f"target_mg ใน config {configured.round(2).tolist()} ต่างจาก COPS train ปัจจุบัน "
                        f"{computed.round(2).tolist()} เกิน {tol_mg} mg -> แก้ config แล้ว clean NHANES ใหม่ทั้งหมด")
    return computed, configured, warn

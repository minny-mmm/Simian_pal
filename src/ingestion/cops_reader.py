"""COPS: <COPS-NN>/Accelerometry/COPS-NN_Day<d>_<h0>h-<h1>h_<side>Wrist.zip (หรือ .csv ที่แตกแล้ว)

1 zip มี csv 1 ไฟล์ อ่านจาก zip ตรง ๆ ไม่ต้องแตก
ไฟล์คั่นด้วย ';' มี header: Time (HH:MM:SS.fff), X, Y, Z, Photo, Temp
ข้อมูลผู้ป่วยอยู่ใน Demographics.csv (คั่นด้วย ';') ทุกคนเป็นผู้ป่วย PD
"""
import re

import numpy as np
import pandas as pd

from src.ingestion.base import DatasetReader, Recording
from src.schema.subject_schema import make_subject_record

FILE_RE = re.compile(
    r"^(COPS-\d+)_Day(\d+)_(\d+)h-(\d+)h_(left|right)Wrist(?:_\d+)?\.(csv|zip)$", re.I
)


class CopsReader(DatasetReader):
    name = "COPS"

    def read_subjects(self):
        path = self.raw_dir / "Demographics.csv"
        demo = pd.read_csv(path, sep=";", dtype={"ID": str}) if path.exists() else pd.DataFrame()
        records = []
        for d in demo.to_dict("records"):
            sid = d["ID"].strip().upper()
            records.append(make_subject_record(
                self.name, sid, sid,
                condition="Parkinson's",   # COPS เป็น cohort ผู้ป่วย PD ทั้งหมด
                age=d["Age"],
                age_at_diagnosis=d["Age"] - d["PD_YearsSinceDiagnosis"],
                sex=d["Sex"],
                handedness=d["Handedness"],
                extra={
                    "pd_subtype": d["PD_Subtype"],
                    "pd_dominant_side": d["PD_DominantSide"],
                    "hoehn_yahr": d["PD_HoehnAndYahr"],
                    "years_since_diagnosis": d["PD_YearsSinceDiagnosis"],
                    "dbs": d["DBS"] == "yes",
                },
                source_file=self.rel(path),
            ))
        # เก็บเฉพาะคนที่มีไฟล์สัญญาณ; คนที่มีไฟล์แต่ไม่อยู่ใน Demographics -> มีแค่ id
        have = {r.subject_id for r in self.list_recordings()}
        records = [r for r in records if r["subject_id"] in have]
        for sid in sorted(have - {r["subject_id"] for r in records}):
            records.append(make_subject_record(self.name, sid, sid))
        return records

    def list_recordings(self):
        recs = {}
        for path in sorted(self.raw_dir.rglob("*")):
            m = FILE_RE.match(path.name)
            if not m:
                continue
            # มีทั้ง .csv และ .zip ชื่อเดียวกัน -> ใช้ .csv
            if path.suffix.lower() == ".zip" and path.with_suffix(".csv").exists():
                continue
            sid, day, h0, wrist = m[1].upper(), int(m[2]), int(m[3]), m[5].lower()
            rid = f"{sid}_Day{day}_{h0:02d}h_{wrist.capitalize()}Wrist"
            if rid in recs:
                raise ValueError(f"{rid}: พบไฟล์ซ้ำ {recs[rid].path} และ {path}")
            recs[rid] = Recording(self.name, sid, rid, path, wrist=wrist, day=day, hour_start=h0)
        return list(recs.values())

    def read_recording(self, rec):
        raw = pd.read_csv(rec.path, sep=";", usecols=["Time", "X", "Y", "Z"])
        tod = pd.to_timedelta(raw["Time"]).dt.total_seconds().to_numpy()
        # เวลาย้อนกลับ = ข้ามเที่ยงคืน -> บวก 1 วัน
        rollover = np.concatenate([[0], np.cumsum(np.diff(tod) < -1)])
        study_time = rec.day * 86400 + tod + rollover * 86400
        return pd.DataFrame({
            "time_s": study_time - study_time[0],
            "study_time_s": study_time,
            "acc_x": raw["X"], "acc_y": raw["Y"], "acc_z": raw["Z"],
        })

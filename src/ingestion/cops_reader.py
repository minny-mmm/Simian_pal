"""COPS: <COPS-NN>/Accelerometry/COPS-NN_Day<d>_<h0>h-<h1>h_<side>Wrist.zip (หรือ .csv ที่แตกแล้ว)

1 zip มี csv 1 ไฟล์ อ่านจาก zip ตรง ๆ ไม่ต้องแตก
ไฟล์คั่นด้วย ';' มี header: Time (HH:MM:SS.fff), X, Y, Z, Photo, Temp
ข้อมูลผู้ป่วยอยู่ใน Demographics.csv (คั่นด้วย ';') ทุกคนเป็นผู้ป่วย PD
label รายชั่วโมงอยู่ใน <COPS-NN>/COPS-NN_symptomdiary.csv 1 แถว = 1 ชั่วโมงของคนนั้น (ใช้กับทั้ง 2 ข้อมือ)
จับคู่กับไฟล์สัญญาณผ่านชื่อไฟล์ในคอลัมน์ WearableData{Left,Right}{CSV,ZIP}
"""
import functools
import re

import numpy as np
import pandas as pd

from src.ingestion.base import DatasetReader, Recording
from src.schema.subject_schema import make_subject_record

FILE_RE = re.compile(
    r"^(COPS-\d+)_Day(\d+)_(\d+)h-(\d+)h_(left|right)Wrist(?:_\d+)?\.(csv|zip)$", re.I
)
DIARY_FILE_COLUMNS = ["WearableDataLeftCSV", "WearableDataLeftZIP",
                      "WearableDataRightCSV", "WearableDataRightZIP"]
# คอลัมน์ใน diary -> คอลัมน์ใน manifest
DIARY_LABELS = {
    "Visit": "visit",                              # inpatient / outpatient / mixed (วันออกจาก รพ.)
    "WearableDataAvailability": "wearable",        # Wearable Worn / Not Worn / Partially Worn
    "Kinesia": "kinesia",                          # รวม Sleep และ No Data
    "KinesiaScore": "kinesia_score",               # -3 (akinesia) .. +3 (dyskinesia)
    "TremorScore": "tremor_score",                 # 0..2
    "FreezingScore": "freezing_score",
    "FallScore": "fall_score",
    "Medication": "medication",                    # ยาที่กินในชั่วโมงนั้น คั่นด้วย ';'
}


class CopsReader(DatasetReader):
    name = "COPS"

    def read_subjects(self):
        path = self.raw_dir / "Demographics.csv"
        demo = pd.DataFrame()
        if path.exists():
            with path.open("rb") as fh:
                demo = pd.read_csv(fh, sep=";", dtype={"ID": str})
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
        return list(self._recordings())

    @functools.lru_cache(maxsize=None)
    def _recordings(self):
        # list ไฟล์ครั้งเดียวต่อ reader (บน S3 การ list แต่ละครั้งคือการเรียกผ่านเน็ต)
        paths = sorted((p for p in self.raw_dir.rglob("*") if FILE_RE.match(p.name)), key=str)
        names = {str(p) for p in paths}
        recs = {}
        for path in paths:
            m = FILE_RE.match(path.name)
            # มีทั้ง .csv และ .zip ชื่อเดียวกัน -> ใช้ .csv
            if path.suffix.lower() == ".zip" and str(path.with_suffix(".csv")) in names:
                continue
            sid, day, h0, wrist = m[1].upper(), int(m[2]), int(m[3]), m[5].lower()
            rid = f"{sid}_Day{day}_{h0:02d}h_{wrist.capitalize()}Wrist"
            if rid in recs:
                raise ValueError(f"{rid}: พบไฟล์ซ้ำ {recs[rid].path} และ {path}")
            recs[rid] = Recording(self.name, sid, rid, path, wrist=wrist, day=day, hour_start=h0)
        return tuple(recs.values())

    def recording_labels(self, recordings):
        by_file = {}
        for path in sorted(self.raw_dir.glob("COPS-*/COPS-*_symptomdiary.csv"), key=str):
            with path.open("rb") as fh:
                diary = pd.read_csv(fh, sep=";", dtype=str, keep_default_na=False)
            for d in diary.to_dict("records"):
                labels = {v: (d[k].strip() or None) for k, v in DIARY_LABELS.items()}
                labels = {k: (None if v in ("NaN", "No Data") and k != "kinesia" else v)
                          for k, v in labels.items()}
                for col in DIARY_FILE_COLUMNS:
                    if d[col].strip():
                        by_file[d[col].strip().rsplit(".", 1)[0].lower()] = labels
        return {r.recording_id: by_file[r.path.stem.lower()]
                for r in recordings if r.path.stem.lower() in by_file}

    def has_raw(self, subject_id):
        return any(r.subject_id == subject_id for r in self.list_recordings())

    def iter_hours(self, subject_id, wrist=None):
        if wrist is None:
            raise ValueError(f"{subject_id}: COPS มี 2 ข้อมือ ต้องระบุ wrist")
        recs = sorted((r for r in self.list_recordings()
                       if r.subject_id == subject_id and r.wrist == wrist.lower()),
                      key=lambda r: (r.day, r.hour_start))
        for rec in recs:
            df = self.read_recording(rec)
            yield (df["study_time_s"].to_numpy(),
                   df[["acc_x", "acc_y", "acc_z"]].to_numpy(np.float32))

    def hour_labels(self, subject_id):
        path = self.raw_dir / subject_id / f"{subject_id}_symptomdiary.csv"
        if not path.exists():
            return None
        with path.open("rb") as fh:
            d = pd.read_csv(fh, sep=";", dtype=str, keep_default_na=False)
        day = d["Day"].str.extract(r"(\d+)")[0].astype(int)
        hour = d["Time"].str.extract(r"(\d+)")[0].astype(int) - 1   # "19 hr" = 18:00-19:00
        kin = d["Kinesia"].str.strip()
        return pd.DataFrame({
            "hour": day * 24 + hour,
            "visit": d["Visit"].str.strip(),
            "diary_sleep": np.where(kin.isin(["No Data", ""]), np.nan, (kin == "Sleep").astype(float)),
        })

    def read_recording(self, rec):
        compression = "zip" if rec.path.suffix.lower() == ".zip" else None
        with rec.path.open("rb") as fh:   # ใช้ได้ทั้งไฟล์ในเครื่องและ s3://
            raw = pd.read_csv(fh, sep=";", usecols=["Time", "X", "Y", "Z"], compression=compression)
        tod = pd.to_timedelta(raw["Time"]).dt.total_seconds().to_numpy()
        # เวลาย้อนกลับ = ข้ามเที่ยงคืน -> บวก 1 วัน
        rollover = np.concatenate([[0], np.cumsum(np.diff(tod) < -1)])
        study_time = rec.day * 86400 + tod + rollover * 86400
        return pd.DataFrame({
            "time_s": study_time - study_time[0],
            "study_time_s": study_time,
            "acc_x": raw["X"], "acc_y": raw["Y"], "acc_z": raw["Z"],
        })

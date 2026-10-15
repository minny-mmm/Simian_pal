"""NHANES 2013–2014 PAX80_H: <raw_dir>/pax80_raw/<SEQN>.tar.bz2 (ActiGraph GT3X+, 80 Hz, ±6 g)

ใน tar: <SEQN>_Logs.csv (ไฟล์แรก) + CSV รายชั่วโมง ~190 ไฟล์เรียงตามเวลา
  GT3XPLUS-AccelerationCalibrated-...<YYYY-MM-DD-HH-MM-SS-mmm>-P0000.sensor.csv
  คอลัมน์ HEADER_TIMESTAMP,X,Y,Z (g) ปีเป็น 2000 เพราะ NHANES ปกปิดวันจริง
แตกแล้วคนละ ~2.4 GB จึงอ่านแบบ stream จาก tar ทีละไฟล์ ไม่แตกลงดิสก์
Logs: DAY_OF_DATA 1 = วันของไฟล์แรก, START_TIME / END_TIME เป็นเวลาของวัน

ตอนนี้ใช้กับขั้น clean (src/clean) เท่านั้น ยังไม่ได้ต่อกับ src/pipeline.py
subject_id ใช้รูปแบบ NHANES-<SEQN>
"""
import io
import tarfile

import numpy as np
import pandas as pd

from src.ingestion.base import DatasetReader


def seqn(subject_id):
    return str(subject_id).split("-")[-1]


class NhanesReader(DatasetReader):
    name = "NHANES"

    def tar_path(self, subject_id):
        return self.raw_dir / "pax80_raw" / f"{seqn(subject_id)}.tar.bz2"

    def has_raw(self, subject_id):
        return self.tar_path(subject_id).exists()

    def iter_hours(self, subject_id, wrist=None):
        day0 = None
        with self.tar_path(subject_id).open("rb") as raw, tarfile.open(fileobj=raw, mode="r|bz2") as tf:
            for member in tf:
                if not member.name.endswith(".sensor.csv"):
                    continue
                # อ่านทีละไฟล์ (~12 MB) เป็น bytes ก่อน: file object แบบ stream ของ tarfile ส่งให้ pandas ตรง ๆ ไม่ได้
                df = pd.read_csv(io.BytesIO(tf.extractfile(member).read()), usecols=["HEADER_TIMESTAMP", "X", "Y", "Z"],
                                 dtype={"X": np.float32, "Y": np.float32, "Z": np.float32})
                ts = pd.to_datetime(df["HEADER_TIMESTAMP"], format="%Y-%m-%d %H:%M:%S.%f")
                if day0 is None:
                    day0 = ts.iloc[0].normalize()
                yield (ts - day0).dt.total_seconds().to_numpy(), df[["X", "Y", "Z"]].to_numpy(np.float32)

    def flag_intervals(self, subject_id):
        log = None
        with self.tar_path(subject_id).open("rb") as raw, tarfile.open(fileobj=raw, mode="r|bz2") as tf:
            for member in tf:   # Logs เป็นไฟล์แรกใน tar จึงหยุดได้เร็ว
                if member.name.endswith("_Logs.csv"):
                    log = pd.read_csv(io.BytesIO(tf.extractfile(member).read()))
                    break
        if log is None or log.empty:
            return np.empty((0, 2))
        day = (log["DAY_OF_DATA"].to_numpy() - 1) * 86400
        start = day + pd.to_timedelta(log["START_TIME"]).dt.total_seconds().to_numpy()
        end = day + pd.to_timedelta(log["END_TIME"]).dt.total_seconds().to_numpy()
        end = np.where(end < start, end + 86400, end)
        return np.c_[start, end]

    def read_subjects(self):
        raise NotImplementedError("NHANES ยังไม่ได้ต่อกับ src/pipeline.py ใช้ src.clean แทน")

    def list_recordings(self):
        raise NotImplementedError("NHANES ยังไม่ได้ต่อกับ src/pipeline.py ใช้ src.clean แทน")

    def read_recording(self, rec):
        raise NotImplementedError("NHANES ยังไม่ได้ต่อกับ src/pipeline.py ใช้ src.clean แทน")

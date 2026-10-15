"""Interface กลางของ reader แต่ละ dataset (adapter pattern)

reader ทุกตัวต้อง:
- read_subjects()   -> list ของ dict ที่สร้างด้วย make_subject_record
- list_recordings() -> list ของ Recording (ยังไม่อ่านสัญญาณ)
- read_recording(r) -> DataFrame ที่มีคอลัมน์ time_s, study_time_s (ถ้ามี)
                       และช่องสัญญาณตามชื่อใน SENSOR_SCHEMA เท่านั้น
- recording_labels(recs) (ไม่บังคับ) -> label ระดับ recording ที่จะเขียนลง manifest

สำหรับขั้น clean (src/clean) reader ต้องมีเพิ่ม:
- has_raw(sid)           -> มีไฟล์ดิบของคนนี้ในเครื่องไหม
- iter_hours(sid, wrist) -> yield (t, xyz) ทีละไฟล์ตามลำดับเวลา
                            t = float64 วินาทีนับจาก 00:00 ของวันแรก, xyz = float32 (n, 3) หน่วย g
- flag_intervals(sid)    -> array (m, 2) ช่วงเวลา [start, end] วินาที ที่แหล่งข้อมูลบอกว่าผิดปกติ
- hour_labels(sid)       -> DataFrame (hour, visit, diary_sleep) หรือ None ถ้าไม่มี diary
"""
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

import numpy as np

from src import storage


@dataclass(frozen=True)
class Recording:
    dataset: str
    subject_id: str
    recording_id: str
    path: Path
    wrist: Optional[str] = None
    task: Optional[str] = None
    day: Optional[int] = None
    hour_start: Optional[int] = None


class DatasetReader:
    name = ""

    def __init__(self, raw_dir, root):
        # raw_dir: path สัมพัทธ์กับ root หรือ s3://bucket/... (อ่านจาก S3 ตรง ๆ)
        self.raw_dir = storage.path(raw_dir, root)
        self.root = Path(root)

    def rel(self, path):
        if storage.is_remote(path):
            return str(path)
        return str(Path(path).relative_to(self.root))

    def read_subjects(self):
        raise NotImplementedError

    def list_recordings(self):
        raise NotImplementedError

    def read_recording(self, rec):
        raise NotImplementedError

    def recording_labels(self, recordings):
        """label ระดับ recording (ถ้ามี) -> {recording_id: {คอลัมน์ใน LABEL_COLUMNS: ค่า}}"""
        return {}

    def has_raw(self, subject_id):
        raise NotImplementedError

    def iter_hours(self, subject_id, wrist=None):
        raise NotImplementedError

    def flag_intervals(self, subject_id):
        return np.empty((0, 2))

    def hour_labels(self, subject_id):
        return None

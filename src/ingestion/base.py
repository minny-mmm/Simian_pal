"""Interface กลางของ reader แต่ละ dataset (adapter pattern)

reader ทุกตัวต้อง:
- read_subjects()   -> list ของ dict ที่สร้างด้วย make_subject_record
- list_recordings() -> list ของ Recording (ยังไม่อ่านสัญญาณ)
- read_recording(r) -> DataFrame ที่มีคอลัมน์ time_s, study_time_s (ถ้ามี)
                       และช่องสัญญาณตามชื่อใน SENSOR_SCHEMA เท่านั้น
"""
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


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
        self.raw_dir = Path(root) / raw_dir
        self.root = Path(root)

    def rel(self, path):
        return str(Path(path).relative_to(self.root))

    def read_subjects(self):
        raise NotImplementedError

    def list_recordings(self):
        raise NotImplementedError

    def read_recording(self, rec):
        raise NotImplementedError

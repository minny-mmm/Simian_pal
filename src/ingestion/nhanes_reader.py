"""NHANES: ยังไม่ได้ทำ (ปิดไว้ใน config.yaml)

เมื่อได้ข้อมูลแล้วให้ implement 3 เมธอดตาม DatasetReader
subject_id ใช้รูปแบบ NHANES-<SEQN>
"""
from src.ingestion.base import DatasetReader


class NhanesReader(DatasetReader):
    name = "NHANES"

    def read_subjects(self):
        raise NotImplementedError("NHANES reader ยังไม่ได้ทำ")

    def list_recordings(self):
        raise NotImplementedError("NHANES reader ยังไม่ได้ทำ")

    def read_recording(self, rec):
        raise NotImplementedError("NHANES reader ยังไม่ได้ทำ")

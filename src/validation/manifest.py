"""Manifest: 1 แถวต่อ 1 recording บอกว่ามาจากไฟล์ไหน เขียนไปที่ไหน และข้อมูลระดับ recording"""
import hashlib

import pandas as pd

MANIFEST_COLUMNS = [
    "recording_id", "dataset", "subject_id", "condition", "task", "wrist", "day", "hour_start",
    "raw_path", "size_mb", "md5", "is_duplicate", "processed_path",
    "n_samples", "duration_s", "fs_measured_hz", "fs_expected_hz",
    "acc_unit", "gyro_unit", "acc_includes_gravity", "status",
]
# label ระดับ recording จาก reader.recording_labels() (ว่างถ้า dataset ไม่มี)
LABEL_COLUMNS = [
    "visit", "wearable", "kinesia", "kinesia_score", "tremor_score",
    "freezing_score", "fall_score", "medication",
]
MANIFEST_COLUMNS += LABEL_COLUMNS


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def to_manifest_frame(rows):
    df = pd.DataFrame(rows, columns=MANIFEST_COLUMNS)
    df = df.astype({"day": "Int16", "hour_start": "Int16", "n_samples": "Int64"})
    dup = df["recording_id"][df["recording_id"].duplicated()]
    if len(dup):
        raise ValueError(f"recording_id ซ้ำ: {sorted(dup.unique())[:10]}")
    return df.sort_values(["dataset", "subject_id", "recording_id"], ignore_index=True)

"""Common schema ของ sensor_data: 1 แถวต่อ 1 sample, 1 ไฟล์ Parquet ต่อ 1 recording

ช่องสัญญาณที่ dataset ไม่มี (เช่น gyro ของ COPS) จะเป็น null
ข้อมูลระดับ recording (หน่วย, sampling rate, gravity) อยู่ใน metadata/manifest.csv
"""
import numpy as np
import pandas as pd
import pyarrow as pa

SENSOR_SCHEMA = pa.schema([
    pa.field("subject_id", pa.string(), nullable=False),
    pa.field("recording_id", pa.string(), nullable=False),
    pa.field("task", pa.string()),                      # ชื่อ task ถ้ามี, null ถ้าบันทึกต่อเนื่อง (COPS)
    pa.field("sample_index", pa.int64(), nullable=False),
    pa.field("time_s", pa.float64(), nullable=False),   # วินาทีนับจาก sample แรกของ recording
    pa.field("study_time_s", pa.float64()),             # วินาทีนับจาก 00:00 ของวันแรก (ถ้ามี)
    pa.field("acc_x", pa.float32()),
    pa.field("acc_y", pa.float32()),
    pa.field("acc_z", pa.float32()),
    pa.field("gyro_x", pa.float32()),
    pa.field("gyro_y", pa.float32()),
    pa.field("gyro_z", pa.float32()),
])

SENSOR_COLUMNS = SENSOR_SCHEMA.names
SIGNAL_COLUMNS = ["acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]


def to_sensor_table(df, subject_id, recording_id, task=None):
    """เติมคอลัมน์ที่ขาดเป็น null, เรียงคอลัมน์ และ cast ตาม SENSOR_SCHEMA"""
    df = df.copy()
    df["subject_id"] = subject_id
    df["recording_id"] = recording_id
    df["task"] = task
    df["sample_index"] = np.arange(len(df), dtype=np.int64)
    for col in SENSOR_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan if col in SIGNAL_COLUMNS else None
    extra = set(df.columns) - set(SENSOR_COLUMNS)
    if extra:
        raise ValueError(f"{recording_id}: มีคอลัมน์ที่ไม่อยู่ใน schema {sorted(extra)}")
    if df["time_s"].isna().any():
        raise ValueError(f"{recording_id}: time_s มีค่าว่าง")
    return pa.Table.from_pandas(df[SENSOR_COLUMNS], schema=SENSOR_SCHEMA,
                                preserve_index=False, safe=True)


def summarize_recording(df):
    """สรุปสั้น ๆ สำหรับ manifest (ไม่ใช่ QC เต็มรูปแบบ)"""
    dt = pd.Series(df["time_s"]).diff().median()
    return {
        "n_samples": len(df),
        "duration_s": round(float(df["time_s"].iloc[-1]), 3) if len(df) else 0.0,
        "fs_measured_hz": round(1 / dt, 2) if dt and dt > 0 else None,
    }

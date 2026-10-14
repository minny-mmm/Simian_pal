"""Common schema ของ subject_metadata: 1 แถวต่อ 1 คน ทุก dataset ใช้คอลัมน์ชุดเดียวกัน"""
import json

import pandas as pd

# ชื่อคอลัมน์ -> dtype ของ pandas (เรียงตามลำดับที่เขียนลงไฟล์)
SUBJECT_COLUMNS = {
    "subject_id": "string",           # unique ข้าม dataset เช่น COPS-2, NHANES-73557
    "dataset": "string",              # COPS / NHANES
    "source_subject_id": "string",    # id เดิมของแหล่งข้อมูล
    "condition": "string",            # ค่ามาตรฐานจาก CONDITIONS
    "condition_raw": "string",        # ค่าเดิมจากแหล่งข้อมูล
    "age_years": "Int16",
    "age_at_diagnosis_years": "Int16",
    "sex": "string",                  # male / female
    "handedness": "string",           # left / right / ambidextrous
    "height_cm": "Float32",
    "weight_kg": "Float32",
    "extra_json": "string",           # ฟิลด์เฉพาะ dataset ที่ไม่มีใน schema กลาง
    "source_file": "string",
}

CONDITIONS = {
    "healthy",
    "parkinsons",
    "essential_tremor",
    "atypical_parkinsonism",
    "multiple_sclerosis",
    "other_movement_disorder",
}

_CONDITION_ALIASES = {
    "healthy": "healthy",
    "parkinson's": "parkinsons",
    "parkinsons": "parkinsons",
    "essential tremor": "essential_tremor",
    "atypical parkinsonism": "atypical_parkinsonism",
    "multiple sclerosis": "multiple_sclerosis",
    "other movement disorders": "other_movement_disorder",
}


def normalize_condition(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    key = str(value).strip().lower()
    if key not in _CONDITION_ALIASES:
        raise ValueError(f"ไม่รู้จัก condition: {value!r} (เพิ่มใน _CONDITION_ALIASES)")
    return _CONDITION_ALIASES[key]


def _lower_or_none(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    return str(value).strip().lower()


def make_subject_record(dataset, source_subject_id, subject_id, *, condition=None,
                        age=None, age_at_diagnosis=None, sex=None, handedness=None,
                        height_cm=None, weight_kg=None, extra=None, source_file=None):
    return {
        "subject_id": subject_id,
        "dataset": dataset,
        "source_subject_id": str(source_subject_id),
        "condition": normalize_condition(condition),
        "condition_raw": condition,
        "age_years": age,
        "age_at_diagnosis_years": age_at_diagnosis,
        "sex": _lower_or_none(sex),
        "handedness": _lower_or_none(handedness),
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "extra_json": json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None,
        "source_file": source_file,
    }


def to_subject_frame(records):
    """แปลง list ของ record เป็น DataFrame ตาม schema และตรวจ key"""
    df = pd.DataFrame(records, columns=list(SUBJECT_COLUMNS))
    df = df.astype(SUBJECT_COLUMNS)
    validate_subjects(df)
    return df.sort_values(["dataset", "subject_id"], ignore_index=True)


def validate_subjects(df):
    missing = set(SUBJECT_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"subject_metadata ขาดคอลัมน์: {sorted(missing)}")
    for col in ("subject_id", "dataset", "source_subject_id"):
        if df[col].isna().any():
            raise ValueError(f"subject_metadata มีค่าว่างในคอลัมน์ {col}")
    dup = df["subject_id"][df["subject_id"].duplicated()]
    if len(dup):
        raise ValueError(f"subject_id ซ้ำ: {sorted(dup.unique())[:10]}")
    bad = set(df["condition"].dropna()) - CONDITIONS
    if bad:
        raise ValueError(f"condition ไม่อยู่ในค่ามาตรฐาน: {sorted(bad)}")

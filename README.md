# Parkinson's wrist-sensor pipeline

รวมข้อมูล accelerometer ที่ข้อมือและข้อมูลผู้เข้าร่วมจาก COPS และ NHANES
ให้อยู่ใน schema เดียวกัน เพื่อนำไปทำ QC และ feature extraction ต่อ

> อยู่ระหว่างเริ่มสร้างใหม่ ส่วนที่ยังไม่เสร็จ: COPS reader ต้องปรับให้รองรับโครงสร้างไฟล์ใหม่,
> NHANES reader ยังไม่ได้ทำ

```
config/config.yaml              ตั้งค่า path, dataset ที่เปิดใช้, หน่วย, sampling rate
data/raw/
  cops/                         COPS-<n>/ (Accelerometry/*.zip, UPDRS, symptom diary) + Demographics.csv
  nhanes/2013-2014/
    health_history/             แบบสอบถามสุขภาพ (.xpt + codebook .htm) ดู README ในโฟลเดอร์
    pax80_raw/                  PAX80_H accelerometer 80 Hz (.tar.bz2)
data/processed/
  subject_data/subject_metadata_<dataset>.csv     1 แถว / 1 คน
  sensor_data/<dataset>/<condition>/<subject_id>/<recording_id>.parquet   1 ไฟล์ / 1 recording
metadata/manifest.csv           1 แถว / 1 recording (สร้างโดย pipeline)
src/
  ingestion/                    reader แยกตาม dataset (adapter) + unzip_cops.py
  schema/                       common schema ของ subject และ sensor
  validation/manifest.py        สร้าง manifest
  pipeline.py                   จุดเริ่มรัน
tests/
```

## วิธีรัน

```bash
pip install -r requirements.txt
python -m src.pipeline                  # ทุก dataset ที่ enabled
python -m src.pipeline --datasets cops  # เฉพาะ dataset ที่เลือก
python -m src.pipeline --force          # เขียน Parquet ทับของเดิม
python -m pytest tests
```

## `subject_metadata_<dataset>.csv`

| คอลัมน์ | ความหมาย |
| --- | --- |
| `subject_id` | key ที่ไม่ซ้ำข้าม dataset: `COPS-2`, `NHANES-<SEQN>` |
| `dataset` | `COPS` / `NHANES` |
| `source_subject_id` | id เดิมของแหล่งข้อมูล |
| `condition` | `healthy`, `parkinsons`, `essential_tremor`, `atypical_parkinsonism`, `multiple_sclerosis`, `other_movement_disorder` |
| `condition_raw` | ค่าเดิมก่อนแปลง |
| `age_years`, `age_at_diagnosis_years` | ปี |
| `sex`, `handedness` | ตัวพิมพ์เล็ก |
| `height_cm`, `weight_kg` | |
| `extra_json` | ฟิลด์เฉพาะ dataset |
| `source_file` | ไฟล์ต้นทาง |

COPS: ทุกคนเป็นผู้ป่วย PD (`condition = parkinsons`), `age_at_diagnosis_years = Age - PD_YearsSinceDiagnosis`,
subtype / dominant side / Hoehn & Yahr / DBS อยู่ใน `extra_json`

## `sensor_data` (Parquet)

| คอลัมน์ | ชนิด | ความหมาย |
| --- | --- | --- |
| `subject_id`, `recording_id` | string | key เชื่อมกับ subject_metadata และ manifest |
| `task` | string | null ถ้าบันทึกต่อเนื่อง |
| `sample_index` | int64 | ลำดับ sample เริ่มที่ 0 |
| `time_s` | float64 | วินาทีนับจาก sample แรกของ recording |
| `study_time_s` | float64 | วินาทีนับจาก 00:00 ของ Day 0 (ถ้ามี) |
| `acc_x/y/z` | float32 | accelerometer (g) |
| `gyro_x/y/z` | float32 | null (COPS และ NHANES ไม่มี gyroscope) |

**ข้อควรระวัง:** COPS บันทึกที่ 100 Hz ส่วน NHANES PAX80 ที่ 80 Hz ต้อง resample ให้ตรงกันก่อนเปรียบเทียบ feature

## เพิ่ม dataset ใหม่

1. สร้าง reader ใน `src/ingestion/` ที่ implement `read_subjects`, `list_recordings`, `read_recording` (ดู `base.py`)
2. ลงทะเบียนใน `src/ingestion/__init__.py`
3. เพิ่มค่าใน `config/config.yaml`

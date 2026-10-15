# Parkinson's wrist-sensor pipeline

เตรียมข้อมูล accelerometer ที่ข้อมือ เพื่อเทรน AI แยกผู้ป่วยพาร์กินสัน (PD) ออกจากคนปกติ

- **PD**: COPS (GENEActiv 100 Hz) ผู้ป่วย 49 คนที่มีวันอยู่บ้าน ≥ 3 วัน
- **คนปกติ**: NHANES 2013–2014 PAX80_H (ActiGraph 80 Hz) 150 คน ที่อายุและเพศตรงกับ COPS

**หลักสำคัญ:** สองชุดใช้นาฬิกาคนละยี่ห้อ โมเดลอาจเรียนรู้ยี่ห้อนาฬิกาแทนโรค
ทุกขั้นจึงใช้โค้ดและเกณฑ์เดียวกันกับทั้งสองชุด ส่วนที่แยกตาม dataset มีแค่การอ่านไฟล์ และ `harmonize` ใน config

## Flow ของโปรเจค

```
 ข้อมูลดิบ                    เตรียมรายชื่อคน                         clean                         ตรวจผล / ต่อไป
 ─────────                    ──────────────                         ─────                         ──────────────
 data/raw/cops/      ─┬─> tools/cops_diary_visits.py
 (zip รายชั่วโมง,      │      -> reports/cops_visit_summary.csv
  diary, demographics)│         (วันบ้าน/รพ., use_for_main_model)
                      │                 │
 data/raw/nhanes/    ─┼─> tools/select_nhanes.py                 ┌─> src/clean/run.py ──────> data/clean/<ds>/<id>.parquet
 (แบบสอบถาม, PAX     │      -> metadata/selected_seqn.csv         │   (อ่านไฟล์ดิบผ่าน           data/clean/<ds>/<id>.qc.json
  summary, tar.bz2)   │         (NHANES 150 คน)                  │    src/ingestion/ แล้วรัน      reports/qc_summary.csv
                      │                 │                         │    src/clean/steps.py)              │
                      │                 v                         │                                     v
                      │      tools/make_splits.py ────────────────┘                      notebooks/clean_check.ipynb
                      │      -> metadata/cops_splits.csv  (split + ข้อมือที่ใช้)                 (ตรวจด้วยตา + LR ร่องรอยเครื่อง)
                      │         + คอลัมน์ split ใน selected_seqn.csv                                  │
                      │                                                                              v
                      └─> src/pipeline.py (ทางเลือก, COPS เท่านั้น)                          feature extraction + โมเดล
                            -> data/processed/ + metadata/manifest.csv                           (ยังไม่ได้เริ่ม)
```

ลำดับการทำงาน

1. **โหลดข้อมูลดิบ** ลง `data/raw/` ด้วย `tools/download_pilot.py` หรือ `tools/s3_sync.py pull`
2. **เลือกคน**
   - COPS: `tools/cops_diary_visits.py` อ่าน symptom diary แล้วนับวันบ้าน/รพ. ใช้คนที่มีวันบ้าน ≥ 3 วัน
   - NHANES: `tools/select_nhanes.py` คัดตามเกณฑ์ (มีข้อมูล, ข้อมือข้างไม่ถนัด, ใส่ ≥ 4 วัน, อายุ 47–72,
     ไม่กินยา PD, ไม่เคยเป็น stroke) แล้วสุ่มให้สัดส่วนเพศ × อายุ ตรงกับ COPS
3. **แบ่ง train / valid / test** ตามคน (70/15/15) ด้วย `tools/make_splits.py`
   ค่ากลางที่คำนวณจากข้อมูล (เช่น noise floor เป้าหมาย) ใช้เฉพาะคนใน train
4. **clean** ด้วย `python -m src.clean.run` อ่านไฟล์ดิบโดยตรง (ไม่ต้องผ่าน `src/pipeline.py`)
   ทำทีละคน ทีละชั่วโมง ติดธงทุก sample แทนการลบ แล้วสรุป QC
5. **ตรวจผล** ใน `notebooks/clean_check.ipynb` (กราฟรายวัน, noise floor, การนอนเทียบ diary, LR ว่าแยกเครื่องได้ไหม)
6. **feature extraction และโมเดล** (ยังไม่ได้เริ่ม) ใช้เฉพาะ sample ที่ `valid = True`

`src/pipeline.py` (ingestion -> common schema + manifest) เป็นเส้นทางแยก ใช้ดูข้อมูลดิบและ label รายชั่วโมงของ COPS
ขั้น clean ไม่ได้ใช้ผลจากส่วนนี้

## โครงสร้างโปรเจค

### โค้ด (`src/`)

| ไฟล์ | หน้าที่ |
| --- | --- |
| `src/ingestion/base.py` | `DatasetReader` คลาสแม่ของ reader ทุกตัว: `has_raw`, `iter_hours` (ส่งสัญญาณทีละชั่วโมง), `flag_intervals` (ช่วงที่แหล่งข้อมูลบอกว่าผิดปกติ), `hour_labels` (label รายชั่วโมง) |
| `src/ingestion/cops_reader.py` | อ่าน COPS จาก zip ตรง ๆ, ต่อเวลาข้ามเที่ยงคืน, ดึง `visit` และ `diary_sleep` จาก symptom diary |
| `src/ingestion/nhanes_reader.py` | อ่าน NHANES แบบ stream จาก `<SEQN>.tar.bz2` ไม่ต้องแตกไฟล์, อ่านช่วงผิดปกติจาก `_Logs.csv` |
| `src/ingestion/unzip_cops.py` | แตก zip ของ COPS เฉพาะคนที่เลือกลง `data/raw/cops` (ไม่จำเป็นสำหรับ clean เพราะ reader อ่าน zip ตรง ๆ ได้) |
| `src/ingestion/__init__.py` | `READERS` ลงทะเบียน reader ตามชื่อ dataset |
| `src/clean/steps.py` | ฟังก์ชันของขั้น clean 2–11 (ไม่มี I/O ทดสอบแยกได้): `TimeFixer`, `Resampler`, calibration, clip, abnormal, ISM, noise harmonize, nonwear, HDCZA, ชั่วโมงใช้ได้ต่อวัน |
| `src/clean/run.py` | รัน clean ทีละคน: อ่านรายชื่อจาก metadata, เรียก steps ตามลำดับ, เขียน parquet + `.qc.json`, รวม `reports/qc_summary.csv` |
| `src/clean/report.py` | อ่าน parquet ที่ clean แล้วเป็น epoch / feature หน้าต่าง 10 วิ (ใช้ใน notebook) |
| `src/pipeline.py` | ingestion: แปลงไฟล์ดิบเป็น common schema + เขียน `metadata/manifest.csv` |
| `src/schema/` | นิยามคอลัมน์ของ subject metadata และ sensor parquet |
| `src/validation/manifest.py` | สร้าง manifest (md5, ขนาดไฟล์, label รายชั่วโมง) |
| `src/storage.py` | อ่าน/เขียนไฟล์ได้ทั้งในเครื่องและ S3 ด้วย API แบบ pathlib เดียวกัน |

### เครื่องมือ (`tools/`)

| ไฟล์ | หน้าที่ |
| --- | --- |
| `download_pilot.py` | โหลดชุด pilot จาก CDC / OSF ตาม `pilot_sample.csv` |
| `pilot_sample.csv` | รายชื่อ pilot 10 คน (NHANES 5, COPS 5) พร้อมเหตุผลที่เลือก |
| `cops_diary_visits.py` | สรุปวันบ้าน/รพ. ของ COPS ทุกคน -> `reports/cops_visit_summary.csv` (+ `use_for_main_model`) |
| `select_nhanes.py` | คัดคนปกติ NHANES 150 คน -> `metadata/selected_seqn.csv` (`--check` ดูจำนวนที่ผ่านแต่ละเกณฑ์) |
| `make_splits.py` | แบ่ง train / valid / test ตามคน -> `metadata/cops_splits.csv`, คอลัมน์ `split` ใน `selected_seqn.csv` |
| `feature_check.py` | ตรวจเร็ว ๆ จากไฟล์ดิบว่า feature หลักไปทิศที่คาดไหม (ยังไม่ผ่าน clean) -> `reports/feature_check*` |
| `s3_sync.py` | sync `data/` กับ S3 (`status` / `push` / `pull`, ข้ามไฟล์ที่ md5 ตรงกัน) |

### ข้อมูลและผลลัพธ์

```
config/config.yaml              ตั้งค่าทั้งหมด: path, dataset, storage (S3), เกณฑ์ทุกขั้นของ clean
data/                           (ไม่อยู่ใน git)
  raw/cops/                     ข้อมูลดิบ COPS (ห้ามแก้)
    COPS-<n>/
      Accelerometry/*.zip       สัญญาณ 1 ชม. ต่อข้อมือต่อไฟล์
      COPS-<n>_symptomdiary.csv label รายชั่วโมง (visit, kinesia, tremor, ยา, การใส่นาฬิกา)
      COPS-<n>_UPDRS_{ON,OFF}.csv  คะแนน UPDRS III ตอนกินยา / ไม่กินยา
      COPS-<n>_Summary.png      ภาพสรุปของแหล่งข้อมูล (.mat / .eps เป็นสำเนาของ .csv / .png)
    Demographics.csv            ข้อมูลผู้ป่วย COPS ทั้ง 66 คน
  raw/nhanes/2013-2014/         ข้อมูลดิบ NHANES (ห้ามแก้)
    health_history/             แบบสอบถามสุขภาพ (.xpt + codebook .htm) ดู README ในโฟลเดอร์
    demographics/DEMO_H.xpt     อายุ เพศ
    pax_summary/PAXHD_H, PAXDAY_H  สถานะ accelerometer และเวลาใส่รายวัน (ใช้คัดคน)
    pax80_raw/<SEQN>.tar.bz2    accelerometer ดิบ 80 Hz ของแต่ละคน
  clean/<dataset>/              ผลขั้น clean: <subject_id>.parquet + <subject_id>.qc.json
  processed/                    ผลจาก src/pipeline.py
    subject_data/subject_metadata_<dataset>.csv     1 แถว / 1 คน
    sensor_data/<dataset>/<condition>/<subject_id>/<recording_id>.parquet   1 ไฟล์ / 1 recording
metadata/
  cops_splits.csv               COPS 49 คน: subject_id, use_wrist, split, เพศ อายุ H&Y ชนิด DBS, pilot
  selected_seqn.csv             NHANES 150 คน: seqn, age, sex, valid_days, age_band, pilot, split
  manifest.csv                  1 แถว / 1 recording (สร้างโดย src/pipeline.py, ไม่อยู่ใน git)
reports/
  cops_visit_summary.csv        จำนวนวันอยู่ รพ. / บ้าน ของ COPS ทุกคน + use_for_main_model
  qc_summary.csv                QC ของขั้น clean 1 แถว / 1 คน
  feature_check/, feature_check_hours.csv   ผลของ tools/feature_check.py
notebooks/clean_check.ipynb     ตรวจผล clean (กราฟรายวัน, ISM, noise floor, การนอน, LR ร่องรอยเครื่อง)
tests/
  test_ingestion.py             reader ของ COPS (zip, ข้ามเที่ยงคืน, diary label)
  test_clean.py                 ขั้น clean 2, 4, 7, 8 (เวลา, resample, ISM/noise, ถอดนาฬิกา)
  test_storage.py               อ่าน/เขียน/sync กับ S3 จำลอง
```

## สถานะ

| ส่วน | สถานะ |
| --- | --- |
| COPS reader | ใช้ได้ อ่านจาก zip ตรง ๆ + ดึง label รายชั่วโมงจาก symptom diary |
| NHANES reader | อ่าน stream จาก tar ได้แล้ว (ใช้ในขั้น clean; `src/pipeline.py` ยังไม่รองรับ) |
| เลือกคน + แบ่ง split | เสร็จแล้ว COPS 49 คน (35/7/7), NHANES 150 คน (106/22/22) |
| ขั้น clean | ใช้ได้ ตรวจกับ 4 คนแล้ว (`notebooks/clean_check.ipynb`) ยังไม่ได้ขยายไปคนอื่น |
| feature extraction | ยังไม่ได้เริ่ม |

ข้อมูลในเครื่องตอนนี้ (ชุด pilot บางส่วน ดู `tools/pilot_sample.csv`):

| dataset | คน | รายละเอียด |
| --- | --- | --- |
| COPS (PD) | COPS-8, COPS-29, COPS-45 | 2 ข้อมือ, 1,014 ไฟล์ชั่วโมง, มีทั้งช่วงอยู่ รพ. และอยู่บ้าน |
| NHANES (คนปกติ) | SEQN 74132 | ไฟล์ accelerometer ดิบ 1 คน + แบบสอบถามสุขภาพทั้งรอบ 2013–2014 |

ผล clean ตอนนี้: COPS-8, COPS-45, NHANES-74132 ใช้ได้; COPS-29 ไม่ผ่าน (วันใช้ได้ 2 วัน < 3)
ยังไม่ได้โหลด: COPS คนอื่นใน `cops_splits.csv` และ NHANES ที่เหลือใน `selected_seqn.csv` (~26 GB)

## วิธีรัน

```bash
pip install -r requirements.txt
python tools/download_pilot.py tools/pilot_sample.csv --list-only   # ดูรายการก่อนโหลด
python tools/download_pilot.py tools/pilot_sample.csv --out data/pilot
python tools/cops_diary_visits.py       # สรุปวันบ้าน/รพ. ของ COPS
python tools/select_nhanes.py           # คัด NHANES (--check ดูอย่างเดียว)
python tools/make_splits.py             # แบ่ง train / valid / test (--check ดูอย่างเดียว)
python -m src.clean.run                 # clean ทุกคนในรายชื่อที่มีไฟล์ดิบ (ข้ามคนที่ทำแล้ว)
python -m src.clean.run --ids COPS-8    # ทีละคน; --force ทำซ้ำ; --summary-only รวม qc_summary
python -m src.pipeline                  # (ทางเลือก) ingestion ทุก dataset ที่ enabled
python -m src.pipeline --datasets cops  # เฉพาะ dataset ที่เลือก; --force เขียน Parquet ทับ
python -m pytest tests
```

โค้ดอยู่ที่ GitHub `6710615243/Simianpal` (private) ข้อมูลทั้งหมดถูกกันไว้ใน `.gitignore`
ต้องโหลดใหม่ด้วย `tools/` เมื่อ clone ไปเครื่องอื่น

## เก็บข้อมูลบน AWS S3

layout บน S3 เหมือนในเครื่องทุกอย่าง: `data/raw/cops/...` ↔ `<s3_root>/data/raw/cops/...`

1. ตั้งค่าใน `config/config.yaml` → `storage.s3_root` (เช่น `s3://my-bucket/pd-pipeline`), `aws_profile`, `region`
   และ login AWS ก่อน (`aws sso login --profile <ชื่อ>`)
2. sync ข้อมูล (เก็บ md5 ใน metadata ของทุกไฟล์ ไฟล์ที่ md5 ตรงกันจะข้าม; ดาวน์โหลดแล้วตรวจ md5 ก่อนแทนที่)

```bash
python tools/s3_sync.py status                 # เทียบในเครื่องกับ S3
python tools/s3_sync.py push --dry-run         # ดูก่อนว่าจะอัปโหลดอะไร
python tools/s3_sync.py push                   # data/raw/cops, data/raw/nhanes, data/clean
python tools/s3_sync.py pull data/clean        # ดาวน์โหลดบางส่วน
```

3. อ่าน raw จาก S3 ตรง ๆ (ไม่ต้องมีไฟล์ในเครื่อง): เปลี่ยน `datasets.<ชื่อ>.raw_dir` เป็น
   `s3://my-bucket/pd-pipeline/data/raw/cops` (หรือ `.../data/raw/nhanes/2013-2014`)
   COPS อ่าน zip และ NHANES อ่าน tar.bz2 แบบ stream จาก S3 ได้เลย
4. อัปโหลดผล clean หลังทำแต่ละคน: `python -m src.clean.run --upload`
   (ผล clean ยังเขียนในเครื่องก่อนเสมอ แล้วค่อยอัปโหลด)

## รูปแบบไฟล์ดิบ

**COPS** (GENEActiv, 100 Hz) ใน zip มี CSV 1 ไฟล์ คั่นด้วย `;`
```
Time;X;Y;Z;Photo;Temp
10:00:00.010;-0.1552;-0.8264;-0.5325;134;27.9
```
`Time` เป็นเวลานาฬิกา (วันอยู่ในชื่อไฟล์ `Day<d>`), X/Y/Z หน่วย g รวมแรงโน้มถ่วง

**NHANES** (ActiGraph GT3X+, 80 Hz) ใน `<SEQN>.tar.bz2` มี CSV รายชั่วโมง ~190 ไฟล์ + `<SEQN>_Logs.csv`
```
HEADER_TIMESTAMP,X,Y,Z
2000-01-06 10:00:00.000,0.745,0.692,-0.279
```
ปี 2000 เป็นวันที่ปลอม (NHANES ปกปิดวันจริง) ใช้ได้แค่เป็นลำดับวัน, X/Y/Z หน่วย g รวมแรงโน้มถ่วง
`_Logs.csv` บอกช่วงข้อมูลผิดปกติ (`CONTIGUOUS_IMPOSSIBLE_GRAVITY`, `ADJACENT_INVALID`) ต่อ `DAY_OF_DATA`
แตกแล้วคนละ ~2.4 GB ควรอ่านแบบ stream จาก tar

## ขั้น clean (`src/clean`)

ทุกเกณฑ์อยู่ใน `config.yaml` -> `clean`

| ขั้น | ทำอะไร |
| --- | --- |
| 1 อ่าน | ทีละไฟล์ชั่วโมง -> เวลา (วินาทีจาก 00:00 วันแรก) + x, y, z float32; COPS ใช้ข้อมือตาม `cops_splits.csv` |
| 2 เวลา | เรียง ลบซ้ำ ช่องว่าง ≤ 1 วิ interpolate, ยาวกว่านั้น = ไม่มีข้อมูล (ไม่ต่อชั่วโมงเข้าหากัน) |
| 3 calibrate | van Hees (GGIR) จากหน้าต่างนิ่ง ไม่ใช้หน้าต่างที่ห่าง 1 g > 0.15 g หรืออยู่ในช่วงที่แหล่งข้อมูลบอกว่าผิดปกติ |
| 4 resample | 50 Hz ด้วย `resample_poly` ทีละ 10 นาที (มี context กันขอบ) |
| 5 clip | ±6 g, ติดธง ≥ 5.9 g |
| 6 ผิดปกติ | ค่าค้าง ≥ 60 วิ, |acc| ~ 0, spike, แรงโน้มถ่วงเป็นไปไม่ได้, `_Logs.csv` ของ NHANES |
| 7 ISM + noise | หน้าต่าง 10 วิ ทุกแกน range ≤ 50 mg หรือ 1 วิ SD < 1 mg (idle mode); เติม noise ให้ NHANES เท่า COPS แล้วปัดเป็นขั้น 3.9 mg ทั้งสองชุด |
| 8 ถอดนาฬิกา | หน้าต่าง 60 นาที (เลื่อน 15) ใน ≥ 2 แกน: SD ค่าดิบ < 13 mg และ range ของค่าเฉลี่ยทุก 1 วิ < 50 mg |
| 9 นอน | HDCZA (van Hees 2018) เทียบกับ `Kinesia == Sleep` ของ COPS |
| 10–11 | ใช้ได้ = ตื่น + ใส่ + ไม่ติดธง + visit = outpatient; วันใช้ได้ ≥ 10 ชม.; คนใช้ได้ ≥ 3 วัน |

ไม่ band-pass ตอน clean (ทำ 0.5–15 Hz ตอนสกัด feature)
calibration หาค่าและปรับที่ 50 Hz ซึ่งให้ผลเท่ากับปรับก่อน resample เพราะทั้งสองขั้นเป็น linear

**ผลลัพธ์** `data/clean/<dataset>/<subject_id>.parquet` (1 row group ต่อวัน)

| คอลัมน์ | ความหมาย |
| --- | --- |
| `time_s` | วินาทีนับจาก 00:00 ของวันแรก (50 Hz) |
| `x`, `y`, `z` | g หลัง calibrate / clip / ปรับ noise (NaN = ไม่มีข้อมูล) |
| `valid` | ใช้เทรนได้ |
| `reason` | ธงที่ทำให้ตัด คั่นด้วย `|`: `missing`, `clip`, `abnormal`, `ism`, `nonwear`, `sleep`, `visit`, `short_day`, `few_days` |
| `day` | วันที่ (เริ่ม 0, แบ่งที่เที่ยงคืน) |
| `visit` | `inpatient` / `outpatient` / `mixed` (NHANES = `outpatient`) |

`.qc.json` ข้างกันเก็บ QC ของคนนั้น (รวมรายวันและรายคืน) และ `reports/qc_summary.csv` รวมทุกคน
ใช้เวลา ~50 วินาทีและ RAM สูงสุด ~2.5 GB ต่อคน, parquet ~130 MB ต่อคน

## `subject_metadata_<dataset>.csv` (จาก `src/pipeline.py`)

| คอลัมน์ | ความหมาย |
| --- | --- |
| `subject_id` | key ที่ไม่ซ้ำข้าม dataset: `COPS-8`, `NHANES-<SEQN>` |
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

## `sensor_data` (Parquet จาก `src/pipeline.py`)

| คอลัมน์ | ชนิด | ความหมาย |
| --- | --- | --- |
| `subject_id`, `recording_id` | string | key เชื่อมกับ subject_metadata และ manifest |
| `task` | string | null ถ้าบันทึกต่อเนื่อง |
| `sample_index` | int64 | ลำดับ sample เริ่มที่ 0 |
| `time_s` | float64 | วินาทีนับจาก sample แรกของ recording |
| `study_time_s` | float64 | วินาทีนับจาก 00:00 ของ Day 0 (ถ้ามี) |
| `acc_x/y/z` | float32 | accelerometer (g) |
| `gyro_x/y/z` | float32 | null (COPS และ NHANES ไม่มี gyroscope) |

## label รายชั่วโมงใน `manifest.csv` (COPS)

ดึงจาก `COPS-<n>_symptomdiary.csv` โดยจับคู่ผ่านชื่อไฟล์ zip/csv (1 ชั่วโมงของไดอารี = ทั้ง 2 ข้อมือ)

| คอลัมน์ | ค่า |
| --- | --- |
| `visit` | `inpatient` (โรงพยาบาล), `outpatient` (บ้าน), `mixed` (วันออกจาก รพ. ไม่รู้ชั่วโมงที่ออก) |
| `wearable` | `Wearable Worn` / `Wearable Partially Worn` (ไฟล์สั้นกว่า 1 ชม.) |
| `kinesia`, `kinesia_score` | `Sleep`, `Good Kinesia`, akinesia (-1..-3), dyskinesia (+1..+3) |
| `tremor_score`, `freezing_score`, `fall_score` | 0..2 / 0 / 0 (ว่าง = No Data) |
| `medication` | ยาที่กินในชั่วโมงนั้น คั่นด้วย `;` |

ในวัน `mixed` ทั้ง 24 ชั่วโมงติดป้าย `mixed` เหมือนกัน จึงแยกช่วงบ้าน/รพ. ในวันนั้นไม่ได้

## เพิ่ม dataset ใหม่

1. สร้าง reader ใน `src/ingestion/` ที่สืบจาก `DatasetReader` (ดู `base.py`)
   - ขั้น clean ใช้: `has_raw`, `iter_hours` (+ `flag_intervals`, `hour_labels` ถ้ามี)
   - `src/pipeline.py` ใช้: `read_subjects`, `list_recordings`, `read_recording`
2. ลงทะเบียนใน `src/ingestion/__init__.py`
3. เพิ่มค่าใน `config/config.yaml` (และ `clean.harmonize` ถ้าต้องปรับ noise / quantize)

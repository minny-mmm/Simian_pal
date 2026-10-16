# Parkinson's wrist-sensor pipeline

เตรียมข้อมูล accelerometer ที่ข้อมือ เพื่อเทรน AI แยกผู้ป่วยพาร์กินสัน (PD) ออกจากคนปกติ

- **PD**: COPS (GENEActiv 100 Hz) ผู้ป่วย 49 คนที่มีวันอยู่บ้าน ≥ 3 วัน
- **คนปกติ**: NHANES 2013–2014 PAX80_H (ActiGraph 80 Hz) 150 คน ที่อายุและเพศตรงกับ COPS

**หลักสำคัญ:** สองชุดใช้นาฬิกาคนละยี่ห้อ โมเดลอาจเรียนรู้ยี่ห้อนาฬิกาแทนโรค
ทุกขั้นจึงใช้โค้ดและเกณฑ์เดียวกันกับทั้งสองชุด ส่วนที่แยกตาม dataset มีแค่การอ่านไฟล์ และ `harmonize` ใน config

## สำหรับเพื่อนในทีม (อ่านก่อน)

**อ่านอะไรก่อน**

| อยากทำอะไร | อ่าน |
| --- | --- |
| ตั้งเครื่องครั้งแรก, login AWS, ดึง/อัปข้อมูลกับ S3 | [`README_Download.md`](README_Download.md) |
| เข้าใจภาพรวมว่าโค้ดไหนทำอะไร | หัวข้อ "Flow ของโปรเจค" และ "โครงสร้างโปรเจค" ด้านล่าง |
| ใช้ผล clean / feature ต่อ (เทรนโมเดล) | หัวข้อ "ขั้น clean" → ผลลัพธ์ และ "feature รายชั่วโมง" |
| แก้เกณฑ์ clean | `config/config.yaml` → `clean` (ไม่ต้องแก้โค้ด) และหัวข้อ "การปรับ noise" |

**ข้อมูลอยู่ที่ไหน**
- โค้ด + config + รายชื่อคน/split → **git** (`git pull` ก่อนเริ่มงานทุกครั้ง)
- ข้อมูลดิบ, ผล clean, feature → **S3** ของทีม (`tools/s3_pull.py` / `tools/s3_batch.py`) ไม่อยู่ใน git
- บน S3 ตอนนี้: raw COPS 42 คน, NHANES 1 คน; clean COPS 4 คน (COPS-4/5/6/8), NHANES 1 คน (74132); feature ยังไม่มี
  ดูสถานะล่าสุดเองได้ด้วย `python tools/s3_pull.py list`

**งานปกติของแต่ละคน**

```bash
git pull                                                     # โค้ด / config / split ล่าสุด
aws sso login --profile minny                                # ถ้า login หมดอายุ
python tools/s3_pull.py list                                 # บน S3 มีอะไรแล้วบ้าง
python tools/s3_batch.py features --ids COPS-9 COPS-10       # clean + feature คนที่ยังไม่มี แล้วอัปให้เอง
python tools/s3_pull.py get --what features                  # ใช้ feature ของทุกคน (รวมของเครื่องอื่น)
```

**กติกาที่ต้องตกลงกัน**
1. **แบ่งคนกันทำ** (เช่นตาม `--ids` หรือ `--split`) ก่อนรัน `s3_batch.py features` จะได้ไม่ทำซ้ำ / อัปทับกัน
2. **COPS กลุ่ม train ก่อน NHANES** เพราะ noise เป้าของ NHANES มาจาก COPS train
   ตอนนี้ COPS train ครบ 35 คนแล้ว เป้าตั้งตายตัวเป็นต่อแกน `target_mg: [4.93, 5.07, 6.00]` ใน config ห้ามแก้โดยไม่บอกทีม
3. **ห้ามแก้ `config.yaml` (ส่วน `clean`), `src/clean/`, `tools/feature_check.py` แล้วรันเงียบ ๆ**
   ผลจะไม่ตรงกับของคนอื่น ถ้าแก้ต้องบอกทีม commit แล้วรันใหม่ทุกคนด้วย `--reclean` (แก้ clean) หรือ `--force` (แก้ feature)
4. **ห้าม commit ข้อมูล** ใช้ `git add <ไฟล์>` ระบุเป็นไฟล์ อย่า `git add .` (`config/config.yaml.bak`, ข้อมูลรายคนจะติดไป)
5. **ห้ามเปลี่ยน split** (`make_splits.py`, `select_nhanes.py`) โดยไม่บอกทีม เปลี่ยนแล้วต้อง clean NHANES ใหม่ทั้งหมด
6. รัน `python -m pytest tests` ก่อน push โค้ด

**แก้อะไรต้องไปที่ไหน**

| อยากเปลี่ยน | ไฟล์ |
| --- | --- |
| เกณฑ์ clean (nonwear, ISM, วันใช้ได้ ฯลฯ) | `config/config.yaml` → `clean` |
| ขั้นตอน clean | `src/clean/steps.py` (ฟังก์ชัน) + `src/clean/run.py` (ลำดับ) + test ใน `tests/test_clean.py` |
| feature ต่อหน้าต่าง 10 วิ / สรุปรายชั่วโมง | `tools/feature_check.py` → `window_features`, `summarize_hour` (ใช้ทั้งใน feature_check และ `s3_batch.py features`) |
| วิธีรวม feature เป็นรายชั่วโมงจากผล clean | `tools/s3_batch.py` → `hour_features` |
| รายชื่อคน / split | `tools/cops_diary_visits.py`, `tools/select_nhanes.py`, `tools/make_splits.py` |
| อ่าน dataset ใหม่ | `src/ingestion/` (ดู "เพิ่ม dataset ใหม่") |
| bucket / profile AWS | `config/config.yaml` → `storage` |

## Flow ของโปรเจค

```
 ต้นทาง                 เตรียมรายชื่อคน (git)                   clean                              feature (ทีละชั่วโมง)
 ──────                 ──────────────────                     ─────                              ─────────────────────
 OSF (COPS)  ─┐   tools/cops_diary_visits.py
 CDC (NHANES)─┤     -> reports/cops_visit_summary.csv
              │   tools/select_nhanes.py
  s3_batch.py │     -> metadata/nhanes_splits.csv
    ingest    │   tools/make_splits.py
              v     -> metadata/cops_splits.csv (+ split)
     S3: data/raw/ ───────────────┐
              │                   v
  s3_pull.py  │          src/clean/run.py  ───────────>  data/clean/<ds>/<id>.parquet ──>  tools/s3_batch.py features
     get      v          (src/ingestion/ อ่านไฟล์ดิบ        + <id>.qc.json                    (hour_features ใช้ฟังก์ชัน
  data/raw/ ในเครื่อง      + src/clean/steps.py)              -> S3 + reports/qc_summary.csv      ของ tools/feature_check.py)
                                                                                                    -> S3: data/features/<ds>/<id>.csv
                                                                                                    -> reports/features_hours.csv
                                                                                                          │
   ตรวจผล: notebooks/clean_check.ipynb (กราฟรายวัน, noise floor, LR ร่องรอยเครื่อง)                         v
   ทางเลือก: src/pipeline.py -> data/processed/ + metadata/manifest.csv (ดูไฟล์ดิบรายชั่วโมง)         โมเดล (ยังไม่ได้เริ่ม)
```

ลำดับการทำงาน

1. **ข้อมูลดิบขึ้น S3** ด้วย `tools/s3_batch.py ingest` (จาก OSF/CDC) แล้วดึงลงเครื่องด้วย `tools/s3_pull.py get`
   (ชุด pilot แรกโหลดด้วย `tools/download_pilot.py`)
2. **เลือกคน**
   - COPS: `tools/cops_diary_visits.py` อ่าน symptom diary แล้วนับวันบ้าน/รพ. ใช้คนที่มีวันบ้าน ≥ 3 วัน
   - NHANES: `tools/select_nhanes.py` คัดตามเกณฑ์ (มีข้อมูล, ข้อมือข้างไม่ถนัด, ใส่ ≥ 4 วัน, อายุ 47–72,
     ไม่กินยา PD, ไม่เคยเป็น stroke) แล้วสุ่มให้สัดส่วนเพศ × อายุ ตรงกับ COPS
3. **แบ่ง train / valid / test** ตามคน (70/15/15) ด้วย `tools/make_splits.py`
   ค่ากลางที่คำนวณจากข้อมูล (เช่น noise floor เป้าหมาย) ใช้เฉพาะคนใน train
4. **clean** ด้วย `python -m src.clean.run` (ในเครื่อง) หรือ `tools/s3_batch.py features` (ดึงจาก S3 ทีละ 3 คนแล้วอัปผลให้)
   อ่านไฟล์ดิบโดยตรง (ไม่ต้องผ่าน `src/pipeline.py`) ทำทีละคน ทีละชั่วโมง ติดธงทุก sample แทนการลบ แล้วสรุป QC
5. **ตรวจผล** ใน `notebooks/clean_check.ipynb` (กราฟรายวัน, noise floor, การนอนเทียบ diary, LR ว่าแยกเครื่องได้ไหม)
6. **feature รายชั่วโมง** ด้วย `tools/s3_batch.py features` ใช้เฉพาะ sample ที่ `valid = True` → `data/features/` บน S3
7. **โมเดล** (ยังไม่ได้เริ่ม) ใช้ `reports/features_hours.csv` หรือ `data/features/` แบ่งตามคอลัมน์ `split`

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
| `src/clean/noise_target.py` | เป้า noise ต่อแกนจาก COPS train: อ่านจาก `.qc.json` หรือวัดจาก parquet (หน้ากากเดียวกับขั้น clean) แล้วเทียบกับ config |
| `src/clean/report.py` | อ่าน parquet ที่ clean แล้วเป็น epoch / feature หน้าต่าง 10 วิ (ใช้ใน notebook) |
| `src/pipeline.py` | ingestion: แปลงไฟล์ดิบเป็น common schema + เขียน `metadata/manifest.csv` |
| `src/schema/` | นิยามคอลัมน์ของ subject metadata และ sensor parquet |
| `src/validation/manifest.py` | สร้าง manifest (md5, ขนาดไฟล์, label รายชั่วโมง) |
| `src/storage.py` | อ่าน/เขียนไฟล์ได้ทั้งในเครื่องและ S3 ด้วย API แบบ pathlib เดียวกัน |

### เครื่องมือ (`tools/`)

| ไฟล์ | หน้าที่ |
| --- | --- |
| `download_pilot.py` | โหลดชุด pilot จาก CDC / OSF ตาม `pilot_sample.csv` |
| `pilot_sample.csv` | รายชื่อ pilot 10 คน (NHANES 5, COPS 5) พร้อมเหตุผลที่เลือก (ไม่อยู่ใน git; ไม่มีไฟล์ = ไม่เลือก pilot ก่อน) |
| `cops_diary_visits.py` | สรุปวันบ้าน/รพ. ของ COPS ทุกคน -> `reports/cops_visit_summary.csv` (+ `use_for_main_model`) |
| `select_nhanes.py` | คัดคนปกติ NHANES 150 คน -> `metadata/nhanes_splits.csv` (`--check` ดูจำนวนที่ผ่านแต่ละเกณฑ์) |
| `make_splits.py` | แบ่ง train / valid / test ตามคน -> `metadata/cops_splits.csv`, คอลัมน์ `split` ใน `nhanes_splits.csv` |
| `feature_check.py` | ตรวจเร็ว ๆ จากไฟล์ดิบว่า feature หลักไปทิศที่คาดไหม (ยังไม่ผ่าน clean) -> `reports/feature_check*` |
| `noise_target.py` | คำนวณเป้า noise ต่อแกนจาก COPS train แล้วเทียบกับ `target_mg` ใน config (รันหลัง clean COPS ใหม่ทุกครั้ง) |
| `s3_sync.py` | sync `data/` กับ S3 (`status` / `push` / `pull`, ข้ามไฟล์ที่ md5 ตรงกัน) |
| `s3_pull.py` | ดึงข้อมูลจาก S3 ลงเครื่อง เลือกคน / ชนิด (raw, clean, features) ได้ COPS โหลดเฉพาะข้อมือที่ใช้ ตรวจ md5 |
| `s3_batch.py` | ทีละ 3 คน: `ingest` โหลดจาก OSF/CDC ขึ้น S3, `features` ดึงจาก S3 -> clean -> feature รายชั่วโมง -> อัปโหลด -> ลบในเครื่อง |

### ข้อมูลและผลลัพธ์

```
README_Download.md              คู่มือตั้งเครื่อง + ดึง/อัปข้อมูลกับ S3 สำหรับคนในทีม
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
  features/<dataset>/<id>.csv   feature 1 แถว = 1 ชั่วโมง (จาก s3_batch.py features)
  _batch/                       ไฟล์ชั่วคราวของ s3_batch.py (ลบเองเมื่อเสร็จ)
  processed/                    ผลจาก src/pipeline.py
    subject_data/subject_metadata_<dataset>.csv     1 แถว / 1 คน
    sensor_data/<dataset>/<condition>/<subject_id>/<recording_id>.parquet   1 ไฟล์ / 1 recording
metadata/                       (อยู่ใน git เฉพาะ cops_splits.csv, nhanes_splits.csv)
  cops_splits.csv               COPS 49 คน: subject_id, use_wrist, split, เพศ อายุ H&Y ชนิด DBS, pilot
  nhanes_splits.csv             NHANES 150 คน: seqn, age, sex, valid_days, age_band, pilot, split
  manifest.csv                  1 แถว / 1 recording (สร้างโดย src/pipeline.py)
  osf_files.csv                 cache รายชื่อไฟล์บน OSF (s3_batch.py ingest)
reports/                        (อยู่ใน git เฉพาะ cops_visit_summary.csv)
  cops_visit_summary.csv        จำนวนวันอยู่ รพ. / บ้าน ของ COPS ทุกคน + use_for_main_model
  qc_summary.csv                QC ของขั้น clean 1 แถว / 1 คน
  features_hours.csv            feature รายชั่วโมงของทุกคนรวมกัน (s3_batch.py features)
  feature_check/, feature_check_hours.csv   ผลของ tools/feature_check.py (จากไฟล์ดิบ ใช้ดูทิศทางเท่านั้น)
notebooks/clean_check.ipynb     (ไม่อยู่ใน git) ตรวจผล clean (กราฟรายวัน, ISM, noise floor, การนอน, LR ร่องรอยเครื่อง)
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
| ขั้น clean | ใช้ได้ ตรวจกับ 4 คนแล้ว (`notebooks/clean_check.ipynb`) บน S3 มีผล clean 5 คน |
| S3 | raw COPS 42 คน, NHANES 1 คน; metadata ยังไม่ได้อัป |
| feature รายชั่วโมง | โค้ดพร้อม (`s3_batch.py features`) ยังไม่มีผลบน S3 |
| โมเดล | ยังไม่ได้เริ่ม |

ข้อมูลในเครื่องตอนนี้ (ชุด pilot บางส่วน ดู `tools/pilot_sample.csv`):

| dataset | คน | รายละเอียด |
| --- | --- | --- |
| COPS (PD) | COPS-8, COPS-29, COPS-45 | 2 ข้อมือ, 1,014 ไฟล์ชั่วโมง, มีทั้งช่วงอยู่ รพ. และอยู่บ้าน |
| NHANES (คนปกติ) | SEQN 74132 | ไฟล์ accelerometer ดิบ 1 คน + แบบสอบถามสุขภาพทั้งรอบ 2013–2014 |

ผล clean ตอนนี้: COPS-8, COPS-45, NHANES-74132 ใช้ได้; COPS-29 ไม่ผ่าน (วันใช้ได้ 2 วัน < 3)
ยังไม่ได้โหลด: COPS คนอื่นใน `cops_splits.csv` และ NHANES ที่เหลือใน `nhanes_splits.csv` (~26 GB)

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

`data/`, `notebooks/`, `tools/pilot_sample.csv` และไฟล์อื่นใน `metadata/`, `reports/` ถูกกันไว้ใน `.gitignore`
รายชื่อคนและ split 3 ไฟล์ (`metadata/cops_splits.csv`, `metadata/nhanes_splits.csv`, `reports/cops_visit_summary.csv`)
อยู่ใน git ทุกคนได้ชุดเดียวกันตอน clone / pull ข้อมูลดิบโหลดจาก S3 (ดู `README_Download.md`)

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

### ตั้งค่า AWS ครั้งแรก (เฉพาะกรณีใช้ bucket ของตัวเอง)

**คนในทีมไม่ต้องทำส่วนนี้** บัญชีทีมเข้าผ่าน SSO และสร้าง IAM user ไม่ได้ ให้ทำตาม `README_Download.md` แทน

1. ติดตั้ง AWS CLI: `brew install awscli`
2. ใน AWS Console → IAM → Users → Create user (เช่น `pd-pipeline`) → Security credentials → Create access key (Command Line Interface)
   แล้วรัน `aws configure --profile pd` ใส่ Access key / Secret key / region `ap-southeast-1`
   (key ถูกเก็บใน `~/.aws/credentials` ไม่อยู่ในโค้ดหรือ git)
3. สร้าง bucket (ชื่อต้องไม่ซ้ำทั้งโลก, ค่าเริ่มต้นของ AWS ปิดการเข้าถึงสาธารณะไว้แล้ว):
   `aws s3 mb s3://<ชื่อ-bucket> --region ap-southeast-1 --profile pd`
4. ให้ user นั้นเข้าถึงได้เฉพาะ bucket นี้: IAM → user → Add permissions → Create inline policy (JSON)
   ```json
   {"Version": "2012-10-17", "Statement": [
     {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::<ชื่อ-bucket>"},
     {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<ชื่อ-bucket>/*"}]}
   ```
5. แก้ `config/config.yaml`: `s3_root: s3://<ชื่อ-bucket>/pd-pipeline`, `aws_profile: pd`, `region: ap-southeast-1`
6. ทดสอบ: `aws s3 ls s3://<ชื่อ-bucket> --profile pd` และ `python tools/s3_sync.py status`

### ดึงข้อมูลจาก S3 ลงเครื่อง (`tools/s3_pull.py`) สำหรับคนในทีม

1. `git clone https://github.com/minny-mmm/Simian_pal.git pd-pipeline && cd pd-pipeline`
   แล้ว `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `brew install awscli` แล้ว `aws configure sso` ใช้ login ของทีม
   (SSO start URL `https://ntt-inc-2026.awsapps.com/start`, client region `ap-southeast-1`)
   ข้อ `Profile name` ให้ตั้งเป็นชื่อเดียวกับ `storage.aws_profile` ใน `config/config.yaml` (ตอนนี้ `minny`)
   login หมดอายุเมื่อไหร่ใช้ `aws sso login --profile minny`
3. ดึงข้อมูล (ตรวจ md5 ทุกไฟล์, ไฟล์ที่มีแล้วข้าม, ไม่โหลด `.mat` เป็นค่าเริ่มต้น)
   COPS โหลดเฉพาะข้อมือที่ pipeline ใช้ (`use_wrist` ใน `metadata/cops_splits.csv`: ซ้าย 48 คน, ขวา 1 คน คือ COPS-5)
   ไฟล์นี้มากับ git แล้ว ถ้ายังไม่มีในเครื่องจะโหลดทั้ง 2 ข้อมือ

```bash
python tools/s3_pull.py list                                  # บน S3 มีใครบ้าง ชนิดไหน ขนาดเท่าไหร่
python tools/s3_pull.py get --metadata                        # รายชื่อคน + split จาก S3 (ไม่จำเป็นถ้า git pull แล้ว)
python tools/s3_pull.py get --ids COPS-4 73558                # ข้อมูลดิบ (73558 = NHANES-73558) COPS โหลดเฉพาะข้อมือที่ใช้
python tools/s3_pull.py get --ids COPS-4 --wrist both         # COPS ทั้ง 2 ข้อมือ (ขนาด 2 เท่า)
python tools/s3_pull.py get --what features                   # feature ทุกคน (ไฟล์เล็ก)
python tools/s3_pull.py get --what clean --dataset cops       # ผล clean ของ COPS ทุกคน
python tools/s3_pull.py get --ids COPS-4 --dry-run            # ดูก่อนว่าจะโหลดอะไร
```

### ทำทีละ 3 คน (`tools/s3_batch.py`)

ไม่ต้องเก็บข้อมูลดิบทุกคนไว้ในเครื่อง (COPS ~750 MB/คน, NHANES ~150 MB/คน) ในเครื่องใช้ที่แค่ทีละ 3 คน

```bash
# ขึ้น S3 ครั้งแรก: ของที่มีในเครื่องแล้ว + รายชื่อ/split ที่เครื่องอื่นต้องใช้
python tools/s3_sync.py push
python tools/s3_sync.py push metadata reports/cops_visit_summary.csv

# ที่เหลือ: โหลดจากต้นทาง (COPS: OSF osf.io/5xvwn, NHANES: CDC) ทีละ 3 คน -> อัปโหลด -> ลบ
python tools/s3_batch.py ingest --dry-run      # ดูรายการไฟล์ก่อน (รายชื่อไฟล์ OSF เก็บไว้ที่ metadata/osf_files.csv)
python tools/s3_batch.py ingest                # ไฟล์ที่ขนาดบน S3 ตรงกันแล้วข้าม รันซ้ำได้

# สกัด feature: ดึงทีละ 3 คน -> clean -> feature -> อัปโหลด -> ลบไฟล์ใหญ่ในเครื่อง
python tools/s3_batch.py features              # ทุกคนที่ยังไม่มี feature บน S3 (หยุดกลางคันแล้วรันใหม่ได้)
python tools/s3_batch.py features --split train
python tools/s3_batch.py features --ids COPS-8 --force   # สกัดใหม่ (ใช้ผล clean เดิมบน S3; --reclean = clean ใหม่ด้วย)
```

- COPS บน OSF เก็บเป็น zip ทั้งคน (`/Data/COPS-<n>.zip` ~800 MB) สคริปต์โหลดมาแตกแล้วอัปทีละไฟล์เข้า `data/raw/cops/<ID>/...`
  ตามชื่อไฟล์ (zip สัญญาณ -> `Accelerometry/`) แล้วเขียน `_source.json` ไว้ รอบหน้าข้ามทั้งคน; ส่งทั้ง 2 ข้อมือ
  แต่ตอน `features` โหลดเฉพาะข้อมือที่ใช้ (`use_wrist`) + symptom diary
- ทำ COPS ก่อน NHANES เพราะ NHANES ใช้ noise floor ของ COPS กลุ่ม train เป็นเป้า (โหลด `.qc.json` ของ COPS จาก S3 ให้เอง)
- ผล: `<s3_root>/data/clean/<ds>/<id>.parquet + .qc.json`, `<s3_root>/data/features/<ds>/<id>.csv`
  ในเครื่องเหลือ `.qc.json`, `data/features/`, `reports/qc_summary.csv`, `reports/features_hours.csv` (รวมทุกคน)
- feature: 1 แถว = 1 ชั่วโมง ใช้เฉพาะ sample ที่ `valid` หน้าต่าง 10 วิ ด้วยฟังก์ชันเดียวกับ `tools/feature_check.py`
  (ENMO, เดิน/การแกว่งแขน, jerk, rest tremor 3.5–7.5 Hz) เก็บชั่วโมงที่มีหน้าต่างใช้ได้ ≥ 30 (5 นาที)
- เวลาโดยประมาณ: NHANES 1 คน ~4 นาที (clean ~3 + feature ~1)

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

ไม่ band-pass ตอน clean (ขั้น feature ตอนนี้ก็ยังไม่มี band-pass: ใช้การหักค่าเฉลี่ย 1 วิ และเลือกช่วงความถี่ในสเปกตรัมแทน)
calibration หาค่าและปรับที่ 50 Hz ซึ่งให้ผลเท่ากับปรับก่อน resample เพราะทั้งสองขั้นเป็น linear

**ผลลัพธ์** `data/clean/<dataset>/<subject_id>.parquet`

**row group ≠ วัน:** โค้ดเขียนทีละวัน แต่ pyarrow ตัด row group เองทุก 1,048,576 แถว (~5 ชม. 49 นาทีที่ 50 Hz)
1 วันจึงมี 4–5 row group และรอยตัดอยู่กลางชั่วโมง (เช่น 05:49, 11:39, 17:28, 23:18)
ถ้าอ่านทีละ row group เพื่อประหยัด RAM **ให้แบ่งวัน / ชั่วโมงจากคอลัมน์ `day` และ `time_s`** ห้ามถือว่า 1 row group = 1 วัน
(ไม่งั้นชั่วโมงที่คร่อมรอยตัดจะแตกเป็น 2 ชิ้น) หรืออ่านทีละวันด้วย filter: `pq.read_table(path, filters=[("day", "=", d)])`

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

## feature รายชั่วโมง (`data/features/<dataset>/<id>.csv`)

สร้างโดย `tools/s3_batch.py features` → `hour_features` จากผล clean (เฉพาะ sample ที่ `valid`)
หน้าต่าง 10 วิ ด้วย `window_features` / `summarize_hour` ของ `tools/feature_check.py`
เก็บเฉพาะชั่วโมงที่มีหน้าต่างใช้ได้ ≥ 30 (5 นาที) และนิ่งไม่เกิน 90% คนที่ไม่ผ่านเกณฑ์วันได้ไฟล์ว่าง

| คอลัมน์ | ความหมาย |
| --- | --- |
| `subject_id`, `dataset`, `group`, `label`, `split` | คน, `cops`/`nhanes`, `PD`/`healthy`, 1 = PD, train/valid/test |
| `day`, `hour`, `valid_min` | วัน (เริ่ม 0), ชั่วโมง (0–23), นาทีที่ใช้ได้ในชั่วโมงนั้น |
| `windows`, `still_frac` | จำนวนหน้าต่าง 10 วิ, สัดส่วนหน้าต่างนิ่ง |
| `enmo_mg` | ความแรงการเคลื่อนไหวเฉลี่ย |
| `walk_windows`, `swing_deg`, `walk_jerk` | จำนวนหน้าต่างเดิน, มุมแกว่งแขนตอนเดิน, jerk ตอนเดิน |
| `move_jerk` | jerk ของหน้าต่างที่ขยับทั้งหมด |
| `rest_windows`, `tremor_windows`, `rest_tremor_ratio`, `rest_peak_hz` | หน้าต่างพัก, หน้าต่างพักที่มีสั่น 3.5–7.5 Hz, สัดส่วนพลังงานช่วงสั่น, ความถี่หลักตอนพัก |

**ใช้เทรน:** แบ่ง train / valid / test ด้วยคอลัมน์ `split` เท่านั้น (แบ่งตามคน ห้ามสุ่มรายชั่วโมง ไม่งั้นคนเดียวกันอยู่ทั้ง train และ test)

## การปรับ noise ระหว่างยี่ห้อนาฬิกา (harmonize)

PD ทุกคนใส่ GENEActiv คนปกติทุกคนใส่ ActiGraph ยี่ห้อกับโรคจึงซ้อนกัน 100%
ถ้าไม่ปรับ โมเดลจะเรียนยี่ห้อนาฬิกาแทนโรคได้ (shortcut learning)

**ทำอะไร:** เติม Gaussian noise ให้ NHANES **ทีละแกน** จน noise floor แต่ละแกนเท่า median ของ COPS กลุ่ม train
แล้วปัดค่าเป็นขั้น 3.9 mg ทั้งสองชุด (`clean.harmonize` ใน config)

GENEActiv มี noise แต่ละแกนไม่เท่ากัน (แกน z สูงสุดในทุกคน) ส่วน ActiGraph แทบไม่มี noise เลย (< 0.2 mg)
ถ้าเติมเท่ากันทุกแกน NHANES จะมี noise "เท่ากันทุกแกน" ซึ่งเป็นลายนิ้วมือยี่ห้ออีกแบบ

noise ที่เติมต่อแกน: `added_a = sqrt(เป้า_a² − noise เดิม_a² − (3.9 / √12)²)` (หักส่วนที่การปัดเป็นขั้นจะเพิ่มให้เอง)
seed = `seed + crc32(subject_id)` รันซ้ำได้ผลเดิม

**เป้า** `target_mg: [4.93, 5.07, 6.00]` (x, y, z) = มัธยฐานของ COPS กลุ่ม train ครบ 35 คน ตั้งตายตัวใน config

```bash
python tools/noise_target.py             # คำนวณเป้าใหม่จาก COPS train แล้วเทียบกับ config (exit 1 = ไม่ตรง)
python tools/noise_target.py --qc-only   # เร็ว: ใช้ค่าใน .qc.json อย่างเดียว
python tools/noise_target.py --write-qc  # qc.json เก่าที่ยังไม่มีค่าต่อแกน: วัดจาก parquet แล้วเขียนเพิ่ม
```

**รันทุกครั้งหลัง clean COPS ใหม่** (แก้ calibration / quantize / nonwear ฯลฯ) ถ้าต่างเกิน 0.1 mg ให้แก้ config แล้ว clean NHANES ใหม่ทั้งหมด
`src.clean.run` เตือนให้เองถ้า clean COPS train ในรอบนั้นแล้วค่าไม่ตรง

**ผลที่วัดได้** (NHANES-74132)

| | ไม่ปรับ | เติมเท่ากันทุกแกน (เดิม) | เติมต่อแกน (ปัจจุบัน) |
| --- | --- | --- | --- |
| noise x, y, z (mg) | 0.01, 0.00, 0.13 | 5.40, 5.39, 5.40 | 4.87, 5.05, 5.98 |
| COPS train 35 คน x, y, z (mg) | 4.86, 5.00, 5.91 | ← | ← |
| LR แยกยี่ห้อจาก SD 3 แกนในช่วงนิ่ง (AUC) | 1.00 | 0.91 | **0.49** (= เดาสุ่ม) |
| LR แยกยี่ห้อจาก feature หน้าต่าง 10 วิ ทั้งวัน (AUC) | 1.00 | ~0.60 | ยังไม่ได้วัดซ้ำ (เป้า < 0.7) |

ค่าใน `.qc.json`: `noise_floor_before_axes_mg`, `noise_added_axes_mg`, `noise_target_axes_mg`, `noise_floor_after_axes_mg`
(ช่องเดิมแบบเฉลี่ย 3 แกน `noise_floor_*_mg` ยังมีอยู่) ถ้าแกนไหน noise เดิมสูงกว่าเป้า จะเตือนและบันทึก `noise_over_target_axes`

**ทำไมสำคัญ:** สองยี่ห้อต่างกันมากที่สุดตอนขยับน้อย
([Mielke et al.](https://nih.brage.unit.no/nih-xmlui/handle/11250/3096131?show=full),
[Rowlands et al.](https://figshare.le.ac.uk/articles/journal_contribution/Raw_Accelerometer_Data_Analysis_with_GGIR_R-package_Does_Accelerometer_Brand_Matter_/10176872))
ซึ่งเป็นช่วงที่แยก PD ได้ดีที่สุด (UK Biobank: low-movement AUC 0.84 vs เดิน 0.69,
[Williamson et al. 2021](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7999802/))

**ข้อจำกัด**
- สัญญาณที่เล็กกว่า ~5 mg หายทั้งสองชุดเท่ากัน
- noise ที่เติมเป็น white Gaussian ซึ่ง spectrum อาจไม่เหมือน noise จริงของ GENEActiv
- แก้ไม่ได้: ActiGraph อ่านค่าต่ำกว่า ~10% ที่ข้อมือ และตัวกรองภายในเครื่องต่างกัน (80 vs 100 Hz) ตอนขยับ feature อาจยังแยกยี่ห้อได้บ้าง
- feature ด้านความถี่ (เช่น tremor 3.5–7.5 Hz) ข้ามยี่ห้อได้ดีกว่าขนาดสัญญาณ
  ([Comparability of measured acceleration](https://ro.uow.edu.au/sspapers/1656))

**ถ้าเปลี่ยนหรือเพิ่มแหล่งข้อมูล**

| กรณี | ต้องทำ |
| --- | --- |
| แหล่งใหม่ noise ต่ำกว่าเป้า | ใส่ชื่อ dataset ใน `harmonize.add_noise.datasets` |
| แหล่งใหม่ noise สูงกว่าเป้า (บางแกน) | โค้ดเติมไม่ได้ในแกนนั้น จะเตือน + บันทึก `noise_over_target_axes` ใน qc ต้องยกเป้าเป็นค่าสูงสุดของทุกแหล่ง เติม noise ให้ COPS ด้วย แล้ว clean + เทรนใหม่ทั้งหมด |
| แหล่งใหม่ความละเอียดหยาบกว่า 3.9 mg | เปลี่ยน `quantize.step` เป็นค่าที่หยาบที่สุด แล้ว clean ใหม่ทั้งหมด |
| ใช้โมเดลกับข้อมูลใหม่ | ต้องใช้เป้า noise ค่าเดียวกับตอนเทรน |

`target_mg` รับได้ 3 แบบ: `[x, y, z]` (ปัจจุบัน), ตัวเลขเดียว (ทุกแกนเท่ากัน), หรือ `auto`
(มัธยฐานต่อแกนจาก `.qc.json` ของ COPS train ในเครื่อง ใช้ได้เฉพาะ qc ที่มี `noise_floor_after_axes_mg`
คือ clean ด้วยโค้ดเวอร์ชันนี้ขึ้นไป) ค่าที่ใช้ต้องเก็บคู่กับโมเดล

**สิ่งที่ต้องทำต่อ**
1. หลังสกัด feature แล้ว ให้รัน LR แยกยี่ห้อบน feature จริงซ้ำ (โดยเฉพาะช่วงนิ่ง) feature ไหนให้ AUC > 0.7 ให้ตัดออกหรือปรับก่อนเทรน
2. ทดสอบกับข้อมูลที่ยี่ห้อไม่ซ้อนกับโรค
   - NHANES ที่กิน levodopa (PD ที่ใส่ ActiGraph) มี 3 คน คือ SEQN 73725, 78144, 78486
     ถ้าโมเดลทายว่าเป็น PD บ่อยกว่าคนปกติ แปลว่าโมเดลเรียนโรคได้จริง จำนวนน้อยมาก ใช้ได้แค่ตรวจสอบเบื้องต้น
     (ropinirole / pramipexole อีก ~20 คนมักใช้รักษาขาอยู่ไม่สุข จึงไม่นับ)
   - ข้อมูลที่ PD กับคนปกติใส่นาฬิการุ่นเดียวกัน เช่น UK Biobank (Axivity) ใช้เป็นชุดทดสอบภายนอก

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

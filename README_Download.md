# ดาวน์โหลด / อัปโหลดข้อมูลกับ S3

คู่มือสำหรับคนในทีมที่จะดึง dataset (COPS / NHANES) จาก AWS S3 ของทีมมาทำงานต่อในเครื่องตัวเอง
และส่งผล clean กลับขึ้น S3 ให้คนอื่นใช้ต่อ (การทำ feature ดู `README_feature.md`)
ทำครั้งแรกประมาณ 15 นาที ครั้งต่อไปแค่ login แล้วสั่งดาวน์โหลด

- **ดาวน์โหลด** (ทุกคน): ขั้นที่ 1–5
- **อัปโหลด** (คนที่ส่งข้อมูลดิบ / รัน clean): ขั้นที่ 6–8

```
 ต้นทาง (OSF / CDC) ──s3_batch.py ingest──>  S3  <──s3_sync.py push── เครื่องที่มีไฟล์อยู่แล้ว
                                              │ ^
                         s3_pull.py get ──────┘ └──── s3_batch.py clean (ดึง raw -> clean แล้วอัปผลให้เอง)
                                              v      src.clean.run --upload
                                         เครื่องเรา
```

| สคริปต์ | ทิศทาง | ใช้เมื่อ |
| --- | --- | --- |
| `tools/s3_pull.py` | S3 → เครื่อง | ดูว่ามีอะไรบน S3 / โหลดข้อมูลดิบ, ผล clean มาใช้ |
| `tools/s3_sync.py` | เครื่อง ⇄ S3 | ไฟล์อยู่ในเครื่องแล้ว อยากอัปขึ้น (`push`) หรือเทียบ (`status`) |
| `tools/s3_batch.py ingest` | ต้นทาง → S3 | ส่งข้อมูลดิบขึ้น S3 ตรงจาก OSF / CDC ทีละ 3 คน ไม่ต้องเก็บไว้ในเครื่อง |
| `tools/s3_batch.py clean` | S3 → clean → S3 | ดึง raw ทีละ 3 คน clean แล้วอัปผล clean ให้อัตโนมัติ |

| | |
| --- | --- |
| บัญชี AWS | NTT - Challenger Team 03 (205506434226) เข้าผ่าน SSO ของงาน |
| หน้า login | https://ntt-inc-2026.awsapps.com/start |
| Bucket | `s3://amzn-a3-simianpal-205506434226-ap-southeast-1-an/pd-pipeline` |
| Region | `ap-southeast-1` (Singapore) |
| Profile | `minny` (ต้องตรงกับ `storage.aws_profile` ใน `config/config.yaml`) |

**บน S3 ตอนนี้ (16 ต.ค. 2026)** ดูล่าสุดเองด้วย `python tools/s3_pull.py list`

| | raw | clean |
| --- | --- | --- |
| COPS train (35) | ✅ 35 | ✅ 35 |
| COPS valid (7) | ✅ 7 | ❌ ยังไม่ได้ทำ |
| COPS test (7) | ❌ ยังไม่ได้ ingest | ❌ |
| NHANES (150) | ✅ 150 (ครบทุก split ณ 25 ต.ค. 2026, 24.5 GB) | 2 คน (73558, 74132) **ใช้สูตร noise เก่า ต้อง clean ใหม่** |
| metadata | ไม่มีบน S3 (ใช้จาก git) | |

ข้อมูลบน S3 จัดโฟลเดอร์แบบเดียวกับในเครื่อง ดาวน์โหลดแล้วไฟล์จะไปอยู่ที่เดียวกันทันที

```
pd-pipeline/
  data/raw/cops/COPS-4/...                         ข้อมูลดิบ COPS (zip รายชั่วโมง + symptom diary + UPDRS)
  data/raw/cops/Demographics.csv
  data/raw/nhanes/2013-2014/pax80_raw/73558.tar.bz2   ข้อมูลดิบ NHANES
  data/clean/<dataset>/<id>.parquet + .qc.json      ผล clean
  metadata/cops_splits.csv, nhanes_splits.csv       รายชื่อคน, split, ข้อมือที่ใช้
  reports/cops_visit_summary.csv                    วันอยู่บ้าน/รพ. ของ COPS
```

---

## ขั้นที่ 1: เตรียมโค้ดและ Python (ครั้งแรกครั้งเดียว)

เปิด Terminal แล้วรันทีละบรรทัด

```bash
git clone https://github.com/minny-mmm/Simian_pal.git ชื่อ
cd pd-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

ถ้าคำสั่ง `source` ทำงาน หน้าบรรทัดจะมี `(.venv)` ขึ้นมา
**ทุกครั้งที่เปิด Terminal ใหม่ ต้อง `cd pd-pipeline` แล้วรัน `source .venv/bin/activate` ก่อนเสมอ**

## ขั้นที่ 2: ติดตั้ง AWS CLI (ครั้งแรกครั้งเดียว)

```bash
brew install awscli
aws --version
```

ถ้าขึ้นเลขเวอร์ชัน (เช่น `aws-cli/2.38.1`) แปลว่าติดตั้งสำเร็จ
ถ้าพิมพ์แล้วขึ้นตัวอักษรแปลก ๆ อย่าง `ฟไห` แปลว่าแป้นพิมพ์ยังเป็นภาษาไทย ให้สลับเป็นภาษาอังกฤษก่อน

## ขั้นที่ 3: สร้าง profile เพื่อ login (ครั้งแรกครั้งเดียว)

บัญชีของทีมเข้าผ่าน SSO **ไม่ต้องสร้าง IAM user หรือ access key** (บัญชีเราไม่มีสิทธิ์สร้างอยู่แล้ว)

```bash
aws configure sso
```

ระบบจะถามทีละข้อ ให้ตอบดังนี้

| คำถาม | ตอบ |
| --- | --- |
| `SSO session name` | `pd` (ชื่อเล่น ตั้งอะไรก็ได้) |
| `SSO start URL` | `https://ntt-inc-2026.awsapps.com/start` (ไม่ต้องมี `#/` ต่อท้าย) |
| `SSO region` | ค่า SSO Region จากหน้า login (ดูวิธีหาด้านล่าง) ถ้าไม่แน่ใจลอง `us-east-1` |
| `SSO registration scopes` | กด Enter |
| (เบราว์เซอร์เปิดขึ้นมา) | กด **Allow access** (`botocore-client-pd` คือ AWS CLI ของเราเอง) |
| เลือก account / role | ถ้ามีมีอันเดียว ระบบเลือกให้เอง (`205506434226` / `ChallengerAccess`) |
| `Default client Region` | `ap-southeast-1` |
| `CLI default output format` | กด Enter |
| `Profile name` | **`minny`** (ต้องตรงกับ config ถ้าตั้งชื่ออื่น ต้องแก้ `aws_profile` ใน `config/config.yaml` ด้วย) |
| `Configure AWS skills and the AWS MCP server ...?` | `n` |

**หา SSO Region:** เปิด https://ntt-inc-2026.awsapps.com/start → กด NTT - Challenger Team 03 →
ตรง `ChallengerAccess` กด **Access keys** → ส่วน "IAM Identity Center credentials" จะมี SSO start URL และ SSO Region
(ใน popup นั้นมี access key ชั่วคราวด้วย ไม่ต้องใช้ เพราะหมดอายุในไม่กี่ชั่วโมง)

**ทดสอบ** ถ้าไม่ขึ้น error แปลว่าใช้ได้

```bash
aws s3 ls s3://amzn-a3-simianpal-205506434226-ap-southeast-1-an/pd-pipeline/ --profile minny
```

## ขั้นที่ 4: ดูว่าบน S3 มีอะไรบ้าง

```bash
python tools/s3_pull.py list
```

ตัวอย่างผล (`metadata: ยังไม่มี` ไม่เป็นไร เพราะ metadata มากับ git)

```
S3: s3://amzn-a3-simianpal-205506434226-ap-southeast-1-an/pd-pipeline  (14142 ไฟล์, รวม 49.14 GB)
  raw      cops    42 คน   33.63 GB  COPS-4, COPS-5, COPS-6, COPS-7, COPS-8, ... (+30)
  raw      nhanes  60 คน   10.41 GB  NHANES-73558, NHANES-73596, ... (+48)
  clean    cops    35 คน    4.79 GB  COPS-4, COPS-5, COPS-6, COPS-8, ... (+23)
  clean    nhanes   2 คน    0.31 GB  NHANES-73558, NHANES-74132
  metadata: ยังไม่มี (เจ้าของรัน s3_sync.py push metadata reports/cops_visit_summary.csv)
```

## ขั้นที่ 5: ดาวน์โหลด

ไฟล์ metadata (`metadata/cops_splits.csv`, `metadata/nhanes_splits.csv`, `reports/cops_visit_summary.csv`)
มากับ git แล้ว (ได้ตอน `git clone` / `git pull`) ไม่ต้องโหลดแยก สคริปต์ใช้ไฟล์นี้รู้ว่าแต่ละคนอยู่ split ไหนและใช้ข้อมือไหน

> **หมายเหตุ:** ข้อความหลัง `#` ในตัวอย่างเป็นคำอธิบาย Terminal บน Mac (zsh) จะไม่ข้ามให้ ตอนคัดลอกให้เอาแค่ส่วนคำสั่ง
> หรือรัน `setopt interactivecomments` ครั้งเดียวก่อน

เลือกโหลดตามที่ต้องการ (ใส่ `--dry-run` ต่อท้ายเพื่อดูรายการก่อนได้ทุกคำสั่ง)
# ที่เป็น ***** คือใช้บ่อย
```bash
python tools/s3_pull.py get --ids COPS-4 COPS-5 73558      # ข้อมูลดิบของคนที่เลือก (73558 = NHANES-73558) *****
python tools/s3_pull.py get --dataset nhanes               # ข้อมูลดิบ NHANES ทุกคนที่มีบน S3 ******
python tools/s3_pull.py get --what clean --dataset cops    # ผล clean ของ COPS ทุกคน
python tools/s3_pull.py get --what clean --ids NHANES-74132 73558 # ผล clean ของแต่ละ id ******
python tools/s3_pull.py get --what raw clean --ids COPS-4  # หลายชนิดพร้อมกัน
python tools/s3_pull.py get --all                          # ข้อมูลดิบทุกคน (ใหญ่มาก ระวังพื้นที่)
```

| ตัวเลือก | ความหมาย |
| --- | --- |
| `--ids` | รหัสคน: `COPS-4`, `NHANES-73558` หรือแค่ `73558` |
| `--dataset` | `cops` หรือ `nhanes` |
| `--what` | `raw` (ค่าเริ่มต้น), `clean` ใส่ทั้งสองได้ |
| `--wrist` | COPS: `auto` (ค่าเริ่มต้น = ข้างที่ pipeline ใช้), `left`, `right`, `both` |
| `--exclude` | นามสกุลที่ไม่โหลด ค่าเริ่มต้น `mat` (สำเนา MATLAB ของ .csv) เช่น `--exclude mat eps` |
| `--metadata` | โหลดรายชื่อคน + split จาก S3 (ไม่จำเป็นถ้า `git pull` แล้ว; ตอนนี้ยังไม่มีบน S3) |
| `--dry-run` | แสดงรายการอย่างเดียว ไม่โหลดจริง |

สิ่งที่สคริปต์ทำให้อัตโนมัติ
- **ข้อมือ:** COPS โหลดเฉพาะข้อมือที่ใช้ (ซ้าย 48 คน, ขวา 1 คนคือ COPS-5) ประหยัดครึ่งหนึ่ง ถ้ายังไม่มี metadata ในเครื่อง จะโหลดทั้งสองข้าง
- **ตรวจไฟล์:** เทียบ md5 ทุกไฟล์กับค่าที่เก็บไว้ตอนอัปโหลด ไฟล์ที่โหลดมาไม่ครบจะไม่ถูกใช้
- **รันซ้ำได้:** ไฟล์ที่มีในเครื่องแล้วและ md5 ตรงกันจะข้าม เน็ตหลุดกลางทางก็รันคำสั่งเดิมต่อได้

ขนาดโดยประมาณ: COPS คนละ ~400 MB (ข้อมือเดียว) / ~800 MB (สองข้อมือ), NHANES คนละ ~150–180 MB,
ผล clean คนละ ~130–160 MB (ผล clean ทุกคน ≈ 30 GB, raw ข้อมือเดียวทุกคน ≈ 46 GB)

## ใช้งานครั้งต่อไป

```bash
cd pd-pipeline
source .venv/bin/activate
aws sso login --profile ชื่อที่ตั้ง      # ถ้า login หมดอายุ (มักเป็นรายวัน)
python tools/s3_pull.py get --ids COPS-7
```

---

# ฝั่งอัปโหลด

ต้องทำขั้นที่ 1–3 (โค้ด, AWS CLI, login) ก่อน ใช้ profile เดียวกัน
ทุกคำสั่งรันซ้ำได้: ไฟล์ที่อยู่บน S3 แล้วจะข้าม

## ขั้นที่ 6: ส่งข้อมูลดิบขึ้น S3

**แบบ A: ส่งตรงจากต้นทาง (แนะนำ)** โหลดจาก OSF (COPS) / CDC (NHANES) ลงโฟลเดอร์ชั่วคราว `data/_batch/`
→ อัปขึ้น S3 → ลบ ทีละ 3 คน ในเครื่องใช้ที่แค่ ~2.5 GB

```bash
python tools/s3_batch.py ingest --dry-run                    # ดูรายการไฟล์และขนาดก่อน *******
python tools/s3_batch.py ingest --ids COPS-8 NHANES-74132    # เฉพาะบางคน ******
python tools/s3_batch.py ingest --dataset nhanes             # NHANES ทุกคนในรายชื่อ
python tools/s3_batch.py ingest                              # ทุกคนในรายชื่อ (COPS 49 + NHANES 150)
```

| ตัวเลือก | ความหมาย |
| --- | --- |
| `--ids`, `--dataset`, `--split` | เลือกคน จากรายชื่อเดียวกับขั้น clean (`metadata/cops_splits.csv`, `nhanes_splits.csv`) |
| `--batch-size` | จำนวนคนต่อกลุ่ม (ค่าเริ่มต้น 3) |
| `--exclude` | นามสกุลที่ไม่อัป ค่าเริ่มต้น `mat` (`--exclude` เปล่า ๆ = อัปทุกไฟล์) |
| `--overwrite` | ส่งใหม่แม้บน S3 มีแล้ว |
| `--refresh-osf` | อ่านรายชื่อไฟล์บน OSF ใหม่ (ปกติใช้ที่ cache ไว้ใน `metadata/osf_files.csv`) |
| `--dry-run` | แสดงรายการอย่างเดียว |

- COPS บน OSF เป็น zip ทั้งคน (~800 MB) สคริปต์แตกแล้วอัปทีละไฟล์ตาม layout ของเรา
  เสร็จแล้วเขียน `_source.json` ไว้ในโฟลเดอร์ของคนนั้นบน S3 รอบหน้าข้ามทั้งคน
- ครั้งแรกที่อ่านรายชื่อไฟล์บน OSF ใช้เวลาหลายนาที
- ถ้าคนนั้นมีไฟล์อยู่ในเครื่องแล้ว สคริปต์จะไม่โหลดซ้ำ และบอกให้ใช้ `s3_sync.py push` แทน

**แบบ B: อัปจากไฟล์ที่มีในเครื่องแล้ว**

```bash
python tools/s3_sync.py status                               # เทียบเครื่องกับ S3 (ไม่ย้ายไฟล์)
python tools/s3_sync.py push --dry-run                       # ดูก่อนว่าจะอัปอะไร
python tools/s3_sync.py push data/raw/cops/COPS-8 --exclude mat eps   # ทีละคน ไม่อัปสำเนา .mat / .eps
python tools/s3_sync.py push                                 # data/raw/cops, data/raw/nhanes, data/clean
```

ทุกไฟล์เก็บ md5 ไว้ใน metadata บน S3 ไฟล์ที่ md5 ตรงกันจะข้าม (`--overwrite` = อัปทับทุกไฟล์)

## ขั้นที่ 7: ดึง raw จาก S3 มา clean แล้วอัปผลขึ้น S3

**`git pull` ก่อนทุกครั้ง** ต้องได้โค้ดที่เติม noise ต่อแกน (commit `65de488`) และคำสั่ง `s3_batch.py clean` (commit `c7cbdf7`)
ถ้าโค้ดเก่า NHANES จะถูก clean ด้วยสูตรเดิม และจะไม่มีคำสั่ง `clean`

**งานที่เหลือตอนนี้ (แบ่งคนกันทำ อย่ารันคนเดียวกันพร้อมกัน)**

```bash
python tools/s3_batch.py clean --dataset cops --split valid    # 1) COPS valid 7 คน (raw มีแล้ว)
python tools/s3_batch.py ingest --dataset cops --split test    # 2) ส่ง raw COPS test 7 คนขึ้นก่อน
python tools/s3_batch.py clean --dataset cops --split test     #    แล้วค่อย clean
python tools/s3_batch.py clean --dataset nhanes                # 3) NHANES (รวม 2 คนที่ผลเก่า) เฉพาะคนที่ raw ขึ้นแล้ว
```

```bash
python tools/s3_batch.py clean --dry-run                      # ดูว่าใครต้อง clean และเพราะอะไร (ไม่โหลดอะไร)
python tools/s3_batch.py clean --ids COPS-7                   # ลอง 1 คนก่อน
python tools/s3_batch.py clean --dataset nhanes               # NHANES ทุกคนที่ยังไม่มีผล clean บน S3 หรือผลเก่า
python tools/s3_batch.py clean                                # ทุกคนที่ยังต้อง clean
```

ใครต้อง clean (ดูจากผลบน S3)
- ยังไม่มี `data/clean/<ds>/<id>.parquet` + `.qc.json` บน S3
- **NHANES ที่ผล clean บน S3 ใช้เป้า noise ไม่ตรงกับ `target_mg` ใน config** (เช่น clean ก่อนเปลี่ยนเป็นต่อแกน) ถือว่าเก่า clean ใหม่ให้เอง
- ใส่ `--force` = clean ใหม่ทุกคนที่เลือก (หลังแก้โค้ด / config ของขั้น clean)

ต่อ 1 กลุ่ม (3 คน) สคริปต์ทำดังนี้
1. โหลดข้อมูลดิบจาก S3 (COPS เฉพาะข้อมือที่ใช้ + symptom diary) ลง `data/_batch/`
2. clean ทีละคน (ใช้ RAM มาก ~2.5 GB ต่อคน; COPS ~1 นาที, NHANES ~3 นาทีต่อคน)
3. อัป `data/clean/<ds>/<id>.parquet` แล้วตามด้วย `.qc.json` ขึ้น S3
4. ลบไฟล์ดิบและ `.parquet` ในเครื่อง (`--keep-clean` = เก็บ parquet ไว้ ถ้าจะใช้ต่อในเครื่องทันที)
   ในเครื่องเหลือแค่ `.qc.json` จะใช้ผล clean ทีหลังให้โหลดด้วย `s3_pull.py get --what clean`

จบแล้วรวม QC ของทุกเครื่องที่อยู่บน S3 เป็น `reports/qc_summary.csv`
ถ้ารอบนั้น clean COPS กลุ่ม train จะเช็กว่าเป้า noise ใน config ยังตรงกับ COPS ไหม และเตือนถ้าไม่ตรง

| ตัวเลือก | ความหมาย |
| --- | --- |
| `--ids`, `--dataset`, `--split`, `--batch-size` | เหมือน ingest |
| `--dry-run` | แสดงรายชื่อคนที่ต้อง clean พร้อมเหตุผล ไม่โหลด ไม่ clean |
| `--force` | clean ใหม่แม้บน S3 มีผลล่าสุดแล้ว |
| `--keep-clean` | เก็บ `.parquet` ไว้ในเครื่อง |

ถ้า raw ของคนนั้นไม่มีบน S3 จะข้าม (`ไม่มีข้อมูลดิบบน S3`) → `ingest` ก่อน หรือถ้ามี raw ในเครื่องใช้ขั้นที่ 8

**เป้า noise ของ NHANES**
noise ของ NHANES ถูกปรับต่อแกนให้เท่า median ของ COPS กลุ่ม train ซึ่งตอนนี้ **clean ครบ 35 คนแล้ว**
และตั้งตายตัวไว้ที่ `clean.harmonize.add_noise.target_mg: [4.93, 5.07, 6.00]` ใน `config/config.yaml`
- รัน NHANES ได้เลย ไม่ต้องรอ COPS แล้ว
- **ห้ามแก้ค่านี้** โดยไม่บอกทีม แก้แล้ว NHANES ทุกคนต้อง clean ใหม่ (`s3_batch.py clean` จับได้เองว่าผลเก่า)
- ถ้า clean COPS ใหม่ (หลังแก้ขั้น clean) ให้รัน `python tools/noise_target.py` เช็กว่าเป้ายังตรงไหม
- NHANES ที่ clean ก่อนแก้เป็นต่อแกน (NHANES-73558, NHANES-74132) ต้อง clean ใหม่
  `python tools/s3_batch.py clean` จับได้เองว่าเป็นผลเก่า (`--dry-run` ขึ้น `เป้า noise เปลี่ยน (None -> [4.93, 5.07, 6.0])`)
- ทุกคนต้องใช้โค้ดและ `config.yaml` เวอร์ชันเดียวกัน (`git pull` ก่อนรัน) ถ้าแก้ขั้น clean ต้อง `--force` ใหม่ทุกคน

## ขั้นที่ 8: อัปผลที่ทำเองในเครื่อง

**ผล clean** ถ้ารัน clean เองด้วย `src.clean.run` (ไม่ผ่าน `s3_batch.py`)

```bash
python -m src.clean.run --ids COPS-8 --upload     # clean แล้วอัป parquet + qc.json ขึ้น S3 ทันที
python tools/s3_sync.py push data/clean           # หรืออัปทีหลังทั้งโฟลเดอร์
```

**metadata (รายชื่อคน + split)** ถ้ารัน `cops_diary_visits.py` / `select_nhanes.py` / `make_splits.py` ใหม่
ต้อง commit + push ขึ้น git (ทุกคน `git pull` ได้ชุดเดียวกัน) และอัปขึ้น S3 ให้ตรงกันด้วย

```bash
git add metadata/cops_splits.csv metadata/nhanes_splits.csv reports/cops_visit_summary.csv
git commit -m "Update subject lists and splits" && git push
python tools/s3_sync.py push metadata/cops_splits.csv metadata/nhanes_splits.csv reports/cops_visit_summary.csv
```

ตอนนี้ metadata ยังไม่เคยอัปขึ้น S3 (ทุกคนใช้จาก git) ถ้าอยากให้ `get --metadata` ใช้ได้ รันคำสั่ง `s3_sync.py push` ข้างบนครั้งเดียว

**เปลี่ยน split หลังเริ่มแล้ว = ต้อง clean NHANES ใหม่ทั้งหมด** (เป้า noise มาจาก COPS train) บอกทีมก่อนทุกครั้ง

---

## แก้ปัญหาที่เจอบ่อย

| ข้อความ | สาเหตุ / วิธีแก้ |
| --- | --- |
| `ModuleNotFoundError: No module named 'numpy'` หรือ `command not found: python` | ยังไม่ได้เปิด `.venv` → รัน `source .venv/bin/activate` |
| `Token has expired` / `The SSO session ... has expired` / `Error when retrieving token` | login หมดอายุ → `aws sso login --profile minny` |
| `The config profile (minny) could not be found` | ตอน `aws configure sso` ตั้ง Profile name เป็นชื่ออื่น → แก้ `aws_profile` ใน `config/config.yaml` ให้ตรง หรือรัน `aws configure sso` ใหม่แล้วตั้งเป็น `minny` |
| `NoSuchBucket` | ชื่อ bucket ใน config ผิด → `aws s3 ls --profile minny` ดูชื่อจริง แล้วแก้ `s3_root` |
| `AccessDenied` | บัญชีที่ login ไม่ใช่ Team 03 หรือไม่มีสิทธิ์ S3 → ติดต่อผู้จัด |
| `ยังไม่ได้ตั้ง storage.s3_root` | `config/config.yaml` ยังเป็นค่าเดิม → ใส่ `s3_root: s3://amzn-a3-simianpal-205506434226-ap-southeast-1-an/pd-pipeline` |
| `ไม่มี raw ของ COPS-x บน S3` | คนนั้นยังไม่ได้อัปขึ้น → ดูรายชื่อที่มีด้วย `python tools/s3_pull.py list` |
| `ข้อมูลดิบมีขนาดใหญ่ ... ระบุ --ids หรือ --dataset` | กันโหลดทั้งหมดโดยไม่ตั้งใจ → ระบุคน หรือใส่ `--all` ถ้าตั้งใจ |
| `ไม่อยู่ในรายชื่อ: [...]` (`s3_batch.py`) | รหัสไม่อยู่ใน `metadata/cops_splits.csv` / `nhanes_splits.csv` → `git pull` หรือเช็กการสะกด (`NHANES-74132` ต้องมี `NHANES-`) |
| `ไม่มีข้อมูลดิบบน S3 (รัน ingest ก่อน)` | คนนั้นยังไม่มี raw บน S3 → `python tools/s3_batch.py ingest --ids <id>` |
| `ไม่พบไฟล์ของ COPS-x บน OSF` | รหัสนี้ไม่มีบน OSF → เช็กรหัส หรือ `--refresh-osf` ถ้าเพิ่งมีไฟล์ใหม่ |
| `ยังไม่มี noise target` | เกิดเฉพาะถ้าตั้ง `target_mg: auto` แล้วในเครื่องไม่มี `.qc.json` ของ COPS train ที่มีค่าต่อแกน → ใช้ค่าตัวเลขใน config ตามเดิม (`git checkout config/config.yaml`) |
| `คำเตือน noise: target_mg ใน config ... ต่างจาก COPS train` | clean COPS ใหม่แล้ว noise เปลี่ยน → รัน `python tools/noise_target.py` แล้วบอกทีมก่อนแก้ config (แก้แล้วต้อง clean NHANES ใหม่ทั้งหมด) |
| `คำเตือน NHANES-x: noise แกน ... สูงกว่าเป้าอยู่แล้ว` | คนนั้น noise เดิมสูงกว่าเป้า เติมไม่ได้ → แจ้งทีม (ปกติ NHANES ไม่ควรเจอ) |
| `invalid choice: 'features'` | คำสั่ง `features` ถูกเอาออกแล้ว → ใช้ `clean` (การทำ feature ดู `README_feature.md`) |
| `zsh: command not found: #` / `zsh: no matches found` | คัดลอกคำอธิบายหลัง `#` มาด้วย → เอาแค่ส่วนคำสั่ง หรือรัน `setopt interactivecomments` |
| `AccessDenied` ตอนอัป | role อ่านได้แต่เขียนไม่ได้ → ติดต่อผู้จัด |
| `มีอยู่ในเครื่องแล้ว ใช้ ... s3_sync.py push ... แทน` (ingest) | ไฟล์ของคนนั้นอยู่ในเครื่องแล้ว → `python tools/s3_sync.py push data/raw/cops/<id>` |
| `datasets.<ชื่อ>.raw_dir (...) ไม่อยู่ใต้ storage.s3_root` | ตั้ง `raw_dir` เป็น `s3://` คนละ bucket กับ `s3_root` → แก้ให้อยู่ใต้ `s3_root` หรือใช้ path ในเครื่อง |

## ข้อควรระวัง

- **อย่าแชร์** access key หรือ Secret (ถ้าเคยกดดูใน popup Access keys) ลงแชทหรือ commit ลง git
  ค่าใน `~/.aws/` เป็นของเครื่องเราเท่านั้น
- ข้อมูลใน `data/` ไม่อยู่ใน git (กันไว้ใน `.gitignore`) ต้องโหลดจาก S3 ด้วยสคริปต์นี้
  ส่วน metadata 3 ไฟล์ (`cops_splits.csv`, `nhanes_splits.csv`, `cops_visit_summary.csv`) อยู่ใน git
  ไฟล์อื่นใน `metadata/` และ `reports/` ไม่อยู่ใน git
- **ค่าใช้จ่าย:** บัญชี AWS เป็นของผู้จัดงาน ค่าเก็บไฟล์ถูก (~95 GB ≈ $2.4/เดือน) ตัวที่แพงคือ **ดาวน์โหลดลงเครื่อง**
  ฟรี 100 GB ต่อเดือนต่อบัญชี (ทั้งทีมใช้ร่วมกัน) เกินแล้ว ~$0.12/GB

  | ทำอะไร | ดาวน์โหลด |
  | --- | --- |
  | `s3_batch.py clean` ครบทุกคน 1 รอบ | ~46 GB |
  | `--force` ทั้งชุดอีกรอบ | อีก ~46 GB |
  | ดึงผล clean ทุกคน (`get --what clean`) | ~30 GB ต่อคน |
  | `get --all --wrist both` | ~65 GB ต่อคน |

  โหลดเท่าที่ใช้ อย่า `--all` / `--force` ทั้งชุดซ้ำหลายรอบ ทุกอย่างรันบนเครื่องเรา (ไม่มี EC2) จึงนับเป็นดาวน์โหลดทั้งหมด
- **อัปทับ = ของเดิมหาย** (S3 ไม่ได้เปิด versioning) ระวัง `--overwrite`, `--force` ใส่ `--dry-run` / ลอง 1 คนก่อน
- ห้ามอัปข้อมูลที่ไม่ใช่ของ dataset ทั้งสองนี้ หรือไฟล์ส่วนตัวขึ้น bucket ของทีม

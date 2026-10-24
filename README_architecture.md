# สถาปัตยกรรมระบบ pakinson

ระบบติดตามผู้ป่วยพาร์กินสันจากนาฬิกาข้อมือ: เก็บสัญญาณการเคลื่อนไหว → วัดตัวชี้วัดรายวัน → เทียบกับกลุ่มอ้างอิงด้วย AI → แสดงให้แพทย์ดูแนวโน้มระหว่างนัด

เอกสารนี้เรียงจากภาพใหญ่ไปภาพย่อย
- **ชั้น 0:** ปัญหาและแนวคิดของระบบ
- **ชั้น 1:** ภาพรวมทั้งระบบในหน้าเดียว
- **ชั้น 2:** แต่ละส่วนทำงานอย่างไร
- **ชั้น 3:** โค้ด ตาราง และ API สำหรับคนพัฒนาต่อ

---

## ชั้น 0: ปัญหาและแนวคิด

**ปัญหา**
- ผู้ป่วยพาร์กินสันพบแพทย์ทุก 3–6 เดือน แพทย์เห็นอาการแค่ช่วงสั้น ๆ ในห้องตรวจ
- อาการ เช่น อาการสั่นขณะพัก หรือการเดินช้าลง เปลี่ยนไปทุกวันและขึ้นกับเวลาที่กินยา

**แนวคิด**
- ใช้นาฬิกาข้อมือวัดการเคลื่อนไหวต่อเนื่องที่บ้าน
- แปลงเป็นตัวชี้วัดรายวัน 3 ตัว ที่แพทย์อ่านเข้าใจ:

| ตัวชี้วัด | ความหมาย |
| --- | --- |
| **Rest Tremor** | สัดส่วนช่วงพักที่มีการสั่นความถี่ 4–6 Hz |
| **Walking Cadence** | จังหวะก้าวขณะเดิน (ก้าว/วินาที) |
| **Activity Level (ENMO)** | ระดับการเคลื่อนไหวโดยรวม |

**หลักการที่ยึดตลอดทั้งระบบ**
- **ไม่วินิจฉัย ไม่ให้คะแนนความรุนแรง** AI บอกได้แค่ว่า "ค่านี้ต่างจากกลุ่มอ้างอิงแค่ไหน" แยกเทียบกับกลุ่มผู้ป่วย PD (COPS) และกลุ่มคนทั่วไป (NHANES) ไม่รวมเป็นคะแนนเดียว
- **ข้อมูลขาด = ขาด** ค่าที่วัดไม่ได้แสดงเป็นช่องว่างพร้อมเหตุผล ไม่แทนด้วย 0
- **ตัวเลขมาจากการคำนวณ ไม่ใช่จาก LLM** LLM เขียนได้แค่คำอธิบายภาษาไทย และมีตัวตรวจไม่ให้ใส่ตัวเลขหรือสรุปเกินข้อมูล
- **กันข้อมูลรั่ว** แบ่งคน train / valid / test ตั้งแต่ต้น ชุด test เปิดได้ครั้งเดียว และค่ากลางทุกค่าคิดจาก train เท่านั้น
- **ความเป็นส่วนตัว** ชื่อและ HN ในระบบเป็นของสมมติ ข้อมูลที่ส่งให้ LLM เป็นตัวเลขที่ไม่ระบุตัวตน

---

## ชั้น 1: ภาพรวมทั้งระบบ

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ ① แหล่งข้อมูล          ② Data pipeline           ③ AI / Analytics          ④ Cloud (AWS)      │
│                                                                                       │
│ COPS (ผู้ป่วย PD)      ingestion                 Daily aggregation         Lambda + API       │
│ GENEActiv 100 Hz ─┐   → clean (QC 11 ขั้น)  ──►  ตัวชี้วัดรายวัน 3 ตัว  ──►  DynamoDB          │
│                   ├─► → feature รายชั่วโมง        │                         Cognito (login)   │
│ NHANES (คนทั่วไป)  │     (window 10 วินาที)       ▼                         Amplify (เว็บ)    │
│ ActiGraph 80 Hz ──┘                            Reference baseline        Secrets Manager    │
│                       เก็บบน S3                 (fit จาก train)           S3                 │
│                                                 │                                     │
│                                                 ▼                                     │
│                                              Analysis: ระยะห่างจาก COPS/NHANES            │
│                                                 │                                     │
│                                                 ▼                                     │
│                                              Explanation: Bedrock Nova Lite            │
│                                              + ตัวตรวจข้อความ (fallback = template)        │
│                                                                                       │
│ ⑤ แอปพลิเคชัน                                                                          │
│   เว็บแพทย์ (docter.html) ── ดูผู้ป่วย, แนวโน้ม, แจ้งเตือน, ผลประเมิน, รายงาน AI, นัดหมาย        │
│   แอปผู้ป่วย (memory-garden.html, ต้นแบบ) ── เกมความจำ, เล่าเรื่อง, วาดภาพ, ทดสอบการเดิน         │
│                                                                                       │
│ ⑥ การตรวจสอบคุณภาพ: unit test ทุกส่วน · backtest (stability / LOO / valid / test ครั้งเดียว)      │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

| ส่วน | ทำอะไร | ทำงานที่ไหน | สถานะ |
| --- | --- | --- | --- |
| ① แหล่งข้อมูล | ข้อมูลวิจัยสาธารณะ 2 ชุด ผู้ป่วย PD 49 คน และคนทั่วไปที่จับคู่อายุ/เพศ 150 คน | OSF, CDC → S3 | เสร็จ |
| ② Data pipeline | อ่านไฟล์ดิบ → clean และติดธงคุณภาพ → feature รายชั่วโมง | เครื่องทีม + S3 | เสร็จ |
| ③ AI / Analytics | สรุปรายวัน → fit กลุ่มอ้างอิง → วัดระยะห่าง → อธิบายด้วย LLM | เครื่องทีม (fit), Lambda + Bedrock (ใช้งาน) | เสร็จ (เดโมถึง 27 ต.ค.) |
| ④ Cloud | API, ฐานข้อมูล, login, hosting | AWS ap-southeast-1 | ใช้งานจริง |
| ⑤ แอป | เว็บแพทย์ต่อ backend ครบแล้ว ส่วนแอปผู้ป่วยยังเป็นต้นแบบหน้าจอ | เบราว์เซอร์ | แพทย์: ใช้งานได้ / ผู้ป่วย: ต้นแบบ |
| ⑥ ตรวจสอบ | test อัตโนมัติ + backtest ความเสถียรของกลุ่มอ้างอิง | เครื่องทีม | เสร็จ (เปิด test แล้ว 1 ครั้ง) |

---

## ชั้น 2: แต่ละส่วนทำงานอย่างไร

### ① แหล่งข้อมูล

| ชุดข้อมูล | ใคร | อุปกรณ์ | ใช้ทำอะไร |
| --- | --- | --- | --- |
| **COPS** (OSF) | ผู้ป่วย PD 49 คนที่มีวันอยู่บ้าน ≥ 3 วัน มี symptom diary รายชั่วโมงและ UPDRS | GENEActiv 100 Hz | กลุ่มอ้างอิงผู้ป่วย PD |
| **NHANES 2013–14** (CDC) | 150 คน อายุ 47–72 ไม่กินยา PD ไม่เคย stroke สุ่มให้สัดส่วนเพศ × อายุตรงกับ COPS | ActiGraph 80 Hz | กลุ่มอ้างอิงคนทั่วไป |

- **แบ่งคน train / valid / test = 70 / 15 / 15** (`tools/make_splits.py`) แบ่งตามคน ไม่ใช่ตามวัน
- **ข้อควรระวังใหญ่:** สองชุดใช้นาฬิกาต่างยี่ห้อ ความต่างระหว่างกลุ่มจึงอาจมาจากอุปกรณ์ ไม่ใช่โรค ทั้งระบบจึงใช้โค้ดและเกณฑ์เดียวกันกับทั้งสองชุด และเลือก "วัดระยะห่าง" แทนการสร้าง classifier แยก PD

### ② Data pipeline (`src/`)

```
ไฟล์ดิบ (zip / tar.bz2) ─► src/ingestion/  อ่านทีละชั่วโมง ต่อเวลา ดึง label จาก diary
                        ─► src/clean/     แก้เวลา → resample 50 Hz → calibrate → clip → ค่าผิดปกติ
                                          → ช่วงนิ่งผิดปกติ (ISM) → ปรับ noise ให้ 2 อุปกรณ์ใกล้กัน
                                          → nonwear → ช่วงหลับ (HDCZA) → ชั่วโมงใช้ได้ต่อวัน
                                          (ติดธงทุก sample แทนการลบ + qc.json ต่อคน)
                        ─► src/features/  window 10 วินาที → เดิน / พัก / สั่น → สรุปรายชั่วโมง
                                          (enmo, step_freq, n_walk, n_rest, n_tremor, ...)
```
- ผลแต่ละขั้นเก็บบน S3 ทีมดึงหรืออัปด้วย `tools/s3_pull.py` และ `tools/s3_batch.py` (ทีละ 3 คน)
- เกณฑ์ทุกขั้นอยู่ใน `config/config.yaml` ปรับได้โดยไม่ต้องแก้โค้ด

### ③ AI / Analytics (`src/reference/`)

**ขั้น A: Daily aggregation** (`daily.py`)
- ใช้เฉพาะชั่วโมง 09–21 น. ที่มีข้อมูล ≥ 20 นาที
- วันใช้ได้ต้องมี ≥ 6 ชั่วโมงและ 360 นาที
- สูตรแต่ละตัว:
  - tremor = sum(n_tremor) / sum(n_rest)
  - cadence = ค่าเฉลี่ยรายชั่วโมงถ่วงด้วย n_walk (ต้องเดิน ≥ 2 ชม. และ 5 นาที)
  - ENMO = ค่าเฉลี่ยรายชั่วโมงถ่วงด้วยจำนวน window
- วันที่ไม่ผ่านเกณฑ์ได้สถานะ `insufficient_data` พร้อมเหตุผล

**ขั้น B: Reference baseline** (`model.py`)
- fit จาก **train เท่านั้น** แยก COPS และ NHANES
- ให้น้ำหนักทุกคนเท่ากัน คนที่มีหลายวันไม่ได้น้ำหนักมากกว่า
- เก็บค่ามัธยฐาน, quantile 5/25/50/75/95, MAD และ scale ทุกไฟล์ล็อกด้วย SHA256

**ขั้น C: Analysis** (`output.py`) ทุกวันของผู้ป่วยได้:
- `signed_distance` = ค่า − มัธยฐานกลุ่ม
- `standardized_deviation` = ระยะห่าง / scale (ไม่ใช่ความน่าจะเป็น)
- การเปลี่ยนจากวันก่อน และ QC
- ส่งออกเป็น JSON schema 1.0.0 ที่ตรวจด้วย jsonschema

**ขั้น D: Explanation**
- ส่งตัวเลข 3 แถวที่ไม่ระบุตัวตนให้ **Amazon Bedrock Nova Lite** เขียนคำอธิบายภาษาไทย
- ตัวตรวจข้อความ (text guard) บังคับว่า:
  - ห้ามมีตัวเลข
  - ทิศทาง สูงกว่า/ต่ำกว่า ต้องตรงกับข้อมูล
  - ห้ามอ้างระดับหรือความรุนแรง
  - ค่า null ต้องบอกว่าวัดไม่ได้
- ไม่ผ่านตัวตรวจ = ใช้คำอธิบายแบบ template และติดป้ายชัดเจน

**Baseline ที่ใช้อยู่**

| baseline | train | ใช้ที่ไหน |
| --- | --- | --- |
| `real-data-pilot-aws-real-pilot-20261024-v1` | COPS 8 / NHANES 8 คน | เดโม AI บน AWS และหน้ารายงานของเว็บแพทย์ (valid: COPS-7, NHANES-81262) |
| full v2 `e2a35a02…` | COPS 31 / NHANES 58 คน | backtest บนชุด test (เปิดครั้งเดียว 24 ต.ค.) ดู [README_reference_baseline.md](README_reference_baseline.md) |

### ④ Cloud (AWS ap-southeast-1)

```
 เบราว์เซอร์แพทย์
    │ 1. login
    ▼
 Amazon Cognito (Hosted UI, PKCE) ──► ID token
    │ 2. เปิดเว็บ + เรียก API พร้อม token
    ▼
 AWS Amplify Hosting (docter.html)        API Gateway (HTTP, จำกัด 10 req/s)
                                                │
                                                ▼
                                    AWS Lambda pd-web-api (FastAPI)
                                    ตรวจ token → ดูได้เฉพาะผู้ป่วยของแพทย์คนนั้น
                                     │                │                   │
                                     ▼                ▼                   ▼
                              DynamoDB pd_*    Secrets Manager     API เดโม AI (Lambda ของทีม)
                              11 ตาราง          (รหัสเข้าเดโม)        → S3 baseline + Bedrock
                              on-demand
```

| บริการ | ใช้ทำอะไร |
| --- | --- |
| Amazon S3 | เก็บข้อมูลดิบ, ผล clean และ feature, baseline, ผลเดโม AI |
| AWS Lambda | backend เว็บแพทย์ (`pd-web-api`) และเดโม AI ของทีม (`pd-reference-demo-v1`) |
| Amazon API Gateway | ทางเข้า API แบบ HTTP พร้อมจำกัดอัตรา |
| Amazon DynamoDB | ข้อมูลผู้ป่วย ค่ารายวัน แจ้งเตือน ผลประเมิน นัดหมาย และผล AI ที่เก็บไว้ |
| Amazon Cognito | login แพทย์ ด้วย token ที่ระบุ `doctor_id` |
| AWS Amplify Hosting | เสิร์ฟหน้าเว็บแพทย์ |
| AWS Secrets Manager | รหัสเข้าเดโม AI (Lambda อ่านเอง ไม่ส่งถึงเบราว์เซอร์) |
| Amazon Bedrock (Nova Lite) | เขียนคำอธิบายภาษาไทย |
| IAM | แต่ละ Lambda มีสิทธิ์เฉพาะที่ใช้ เช่น ตาราง `pd_*` และ secret ตัวเดียว |
| SageMaker | เตรียม training job ไว้แล้ว แต่ยังไม่ได้รัน เพราะบัญชีรอโควตา instance |

**ความปลอดภัยและค่าใช้จ่าย**
- ทุก API ต้องมี token
- CORS อนุญาตเฉพาะโดเมนเว็บ
- จำกัดอัตรา request ที่ API Gateway
- DynamoDB จ่ายตามการใช้งาน
- เดโม AI จำกัด 100 request และ LLM 20 ครั้งต่อวัน

### ⑤ แอปพลิเคชัน

**เว็บแพทย์** (`frontend/public/docter.html`) ใช้งานจริง ต่อ backend ครบ

| หน้า | สิ่งที่แพทย์เห็น |
| --- | --- |
| หน้าหลัก | ภาพรวมผู้ป่วยทั้งหมด + แจ้งเตือนล่าสุด |
| ผู้ป่วย | รายชื่อ แยกแท็บ ใส่นาฬิกา / ไม่ใส่ / ต้องติดตาม พร้อมแนวโน้ม 7 วัน |
| โปรไฟล์ผู้ป่วย | การ์ดสรุป 7 วัน (ค่ารวม + วันล่าสุด) และกราฟอาการสั่น |
| ข้อมูลจากอุปกรณ์ | ชั่วโมงใส่นาฬิกา คุณภาพสัญญาณ และรายละเอียดนาฬิกา |
| ประเมินโดยแพทย์ | MDS-UPDRS III / MoCA / CDR และบันทึกผลใหม่ |
| รายงาน (AI) | กราฟเทียบ COPS / NHANES พร้อมแถบ Q25–Q75 ตาราง Δ / z และคำอธิบาย LLM ทีละวัน |
| แจ้งเตือน | ค่าแย่กว่าค่าปกติของผู้ป่วยเองเกิน 20% ติดกัน 2 วัน |
| นัดหมาย / ตั้งค่า | ปฏิทินนัด, ธีม, ภาษา, ออกจากระบบ |

**แอปผู้ป่วย** (`frontend/public/memory-garden.html`) เป็นต้นแบบหน้าจอ ยังไม่ต่อ backend
- สมัครและเริ่มใช้งาน
- เกมความจำ, เล่าเรื่อง (เสียง), วาดภาพ, ทดสอบการเดิน
- เชื่อมนาฬิกา, รายงาน และแจ้งเตือน

**การแจ้งเตือนมีสองแบบ อย่าสับสน**
- แจ้งเตือนในเว็บเทียบกับ "ค่าปกติของผู้ป่วยเอง" (มัธยฐาน 3 วันแรก)
- หน้ารายงาน AI เทียบกับ "กลุ่มอ้างอิง" ทั้งสองแบบไม่ใช่การวินิจฉัย

### ⑥ การตรวจสอบคุณภาพ

| ระดับ | ทำอะไร | ที่ไหน |
| --- | --- | --- |
| Unit test | pipeline, clean, reference, output schema, auth, การเชื่อม AI, สูตรค่ารายวัน | `tests/`, `web/backend/tests/` |
| ความตรงกันข้ามระบบ | ค่ารายวันในเว็บ = ค่าในเดโม AI ทุกตัว และ analysis บน AWS = ผลในเครื่อง (ต่างสูงสุด 0.0) | `make_daily_rows.py --check`, `README_reference_deployment.md` |
| Backtest | evaluability → stability (jackknife / bootstrap) → leave-one-out → valid → test ครั้งเดียว | `backtest/` ดู [backtest/README.md](backtest/README.md) |
| กันข้อมูลรั่ว | hash โค้ดและ baseline ก่อน/หลังรัน, test ล็อกจนกว่าจะพร้อม, ห้ามใช้ผล test ปรับโมเดล | `backtest/RUNBOOK.md` |

---

## ชั้น 3: โค้ดและรายละเอียดสำหรับคนพัฒนาต่อ

### แผนที่โฟลเดอร์ทั้ง repo

```
src/                    ② pipeline + ③ AI: ingestion/, clean/, features/, reference/, schema/, storage.py
tools/                  งานรอบ ๆ pipeline: เลือกคน, split, S3, audit, fit / deploy reference
config/                 config.yaml (pipeline), reference_*.yaml, sagemaker_*.yaml
metadata/               รายชื่อคนและ split (cops_splits.csv, nhanes_splits.csv)
tests/                  test ของ pipeline และ reference
backtest/               ⑥ backtest แยกโฟลเดอร์ อ่าน src/ อย่างเดียว
aws/reference/          Docker สำหรับ SageMaker training
aws/demo/               โค้ดเดโม AI บน Lambda (app.py, หน้าเดโม) deploy ด้วย tools/deploy_reference_demo.py
web/backend/            ④ backend เว็บแพทย์ (รายละเอียดด้านล่าง)
frontend/public/        ⑤ docter.html (เว็บแพทย์), memory-garden.html (แอปผู้ป่วย), docter_2.html (แบบร่างหน้ารายงาน)
results/                สรุปผล backtest ที่เผยแพร่ได้ (ไม่มีข้อมูลรายคน)
data/, reports/         ข้อมูลและผลรายคน (ไม่อยู่ใน git)
```

เอกสารแต่ละส่วน:
- [README.md](README.md): pipeline
- [README_feature.md](README_feature.md): feature
- [README_reference_baseline.md](README_reference_baseline.md): reference และผล test
- [README_reference_deployment.md](README_reference_deployment.md): เดโม AI
- [README_demo_usage.md](README_demo_usage.md): วิธีใช้เดโม
- [backtest/README.md](backtest/README.md): backtest
- [README_Download.md](README_Download.md): ตั้งเครื่องและดึงข้อมูล

### Backend เว็บแพทย์ (`web/backend/`)

```
app/
  main.py               FastAPI + router ทุกตัว + CORS + handler ของ Lambda (Mangum)
  auth.py               ตรวจ ID token ของ Cognito → doctor_id (รันในเครื่องโดยไม่ตั้ง COGNITO_* = โหมด dev)
  db.py                 ต่อ DynamoDB + แปลง Decimal
  metrics.py            หน่วยที่แสดง, ค่ารวมหลายวันแบบถ่วงน้ำหนัก, การ์ดสรุป, กราฟ
  reference.py          เรียกเดโม AI: รหัสจาก Secrets Manager, timeout 25 วินาที, cache 1 ชม., error ภาษาไทย
  router/               1 ไฟล์ต่อกลุ่ม endpoint
database/
  data.py               ข้อมูลเดโมทุกตาราง
  make_daily_rows.py    คำนวณค่ารายวัน + จำนวน window จากผล pipeline (--check เทียบกับเดโม AI)
  load_dynamodb.py      create / load / check / on-demand ตาราง pd_*
  import_ai_results.py  ดึงผล AI ที่เดโมเก็บไว้ใน S3 ใส่ pd_ai_results (ไม่ใช้โควตา)
deploy/
  deploy_lambda.py      build + role + Lambda + API Gateway + env (--stage config = แค่สิทธิ์และ env)
  deploy_frontend.py    อัปโหลด docter.html ขึ้น Amplify (หยุดเองถ้าหน้าไม่ได้ชี้ API บน AWS)
  setup_cognito.py      user pool, Hosted UI, app client, บัญชีแพทย์
  brand_cognito.py      โลโก้ + สีของหน้า login Cognito (cognito_logo.png)
  deployment.json       URL / ID ของ resource ที่ deploy แล้ว
tests/                  test_auth.py, test_ai.py, test_metrics.py (ของปลอมทั้งหมด ไม่ต่อ AWS)
```

### API

ทุก endpoint ยกเว้น `/api/health` และ `/api/auth/config` ต้องมี ID token ดูรายละเอียดได้ที่ `/docs` ตอนรันในเครื่อง

| กลุ่ม | Endpoint |
| --- | --- |
| login | `GET /api/auth/config` |
| หน้าหลัก | `GET /api/dashboard/summary` |
| ผู้ป่วย | `GET/POST /api/patients`, `GET /api/patients/{id}`, `/{id}/summary`, `/{id}/metrics` |
| อุปกรณ์ | `GET /api/patients/{id}/device`, `/{id}/data-quality` |
| ผลประเมิน | `GET/POST /api/patients/{id}/assessments` |
| AI | `GET /api/patients/{id}/ai/analysis`, `/ai/explanation?date=`, `/ai/explanations` |
| แจ้งเตือน | `GET /api/alerts`, `PATCH /api/alerts/{patient_id}/{created_at}` |
| นัดหมาย | `GET/POST /api/appointments`, `PATCH /api/appointments/{date}/{time}/{patient_id}` |
| แพทย์ | `GET /api/doctors/me`, `GET /api/me`, `GET/PUT /api/me/settings` |
| รายงานเดิม | `GET /api/reports/overview`, `/percentiles`, `/scatter` (หน้าเว็บไม่ได้ใช้แล้ว) |

### ตาราง DynamoDB

| ตาราง | key | เก็บอะไร |
| --- | --- | --- |
| `pd_doctors` | doctor_id | ชื่อ ตำแหน่ง โรงพยาบาล การตั้งค่า |
| `pd_patients` | patient_id (index `by_doctor`) | HN, ชื่อสมมติ, เพศ, ปีเกิด, สถานะ, ต้องติดตาม, `ai_subject`, ค่าปกติของตัวเอง |
| `pd_daily_metrics` | patient_id + date | ตัวชี้วัด 3 ตัว, ชั่วโมงใส่ / ไม่มีสัญญาณ / คุณภาพต่ำ, QC, `source_day`, จำนวน window, `qc_reasons` |
| `pd_reference` | metric + cohort | มัธยฐาน + Q25/Q75 ของ COPS / NHANES จาก baseline ของเดโม |
| `pd_alerts` | patient_id + created_at (index `by_doctor`) | แจ้งเตือน |
| `pd_notes` | patient_id + date | บันทึก / ปรับยา |
| `pd_devices` | patient_id | รุ่นนาฬิกา, SN, เฟิร์มแวร์, ซิงก์ล่าสุด |
| `pd_assessments` | patient_id + type_date | ผลประเมินทางคลินิก |
| `pd_appointments` | doctor_id + slot | นัดหมาย |
| `pd_hourly_features` | subject_id + hour_idx | feature รายชั่วโมงจาก pipeline |
| `pd_ai_results` | patient_id + result_key | ผล AI: `analysis`, `explanation#<วันที่>` (JSON ตามที่เดโมตอบ) |

### ข้อมูลเดโมในเว็บแพทย์

| ผู้ป่วยในเว็บ | ข้อมูลจริงจาก | สถานะ | ในเดโม AI |
| --- | --- | --- | --- |
| HN-000001 | COPS-7 หญิง 65 ปี H&Y 3, DBS | PD | `VALID-A` (day 3–6) |
| HN-000002 | NHANES-81262 หญิง 71 ปี | ปกติ | `VALID-B` (day 1–7) |
| HN-000003 | NHANES-73558 ชาย 54 ปี | ปกติ | ไม่มี (เป็นคนใน train ที่ใช้ fit baseline) |

- **ค่าการวัดมาจากข้อมูลวิจัยจริง** ส่วนชื่อ, HN และผลประเมินทางคลินิกเป็นของสมมติ
- **ค่ารายวันในเว็บ = ค่าในเดโม AI ทุกตัว** map วันด้วย `source_day`
- **ผล AI อ่านจาก `pd_ai_results` ก่อน** ถ้าไม่มีจึงเรียกเดโม แล้วบันทึกไว้ (เก็บเฉพาะคำอธิบายจาก LLM) หน้ารายงานจึงใช้ได้แม้โควตาเต็ม หรือหลังเดโมหมดอายุ **27 ต.ค. 2026 08:52 น.**

### งานที่ทำบ่อย (เว็บแพทย์)

```bash
cd web/backend
python -m pytest tests -q                                           # test (ไม่ต่อ AWS)
uvicorn app.main:app --reload                                       # รันในเครื่อง
cd database && AWS_PROFILE=minny python load_dynamodb.py load pd_daily_metrics   # โหลดตารางที่แก้ใน data.py
AWS_PROFILE=minny python import_ai_results.py --write                # ดึงผล AI ที่เดโมเก็บไว้
cd .. && AWS_PROFILE=minny python deploy/deploy_lambda.py            # deploy backend
AWS_PROFILE=minny python deploy/deploy_frontend.py                  # deploy หน้าเว็บ
```

SSO หมดอายุ (`TokenRetrievalError`) ให้รัน `aws sso login --profile minny`

### ข้อควรระวัง

- **ห้ามใส่รหัสผ่านหรือ secret ใน repo** รหัสเข้าเดโม AI อยู่ใน Secrets Manager เท่านั้น
- **ห้ามแก้หรือลบ resource ของเดโม AI** (`pd-reference-demo-v1*`) จาก repo ส่วนเว็บ
- **ห้ามใช้ชุด test** ปรับโมเดล, config หรือ threshold เพราะเปิดไปแล้ว 1 ครั้ง
- **ห้ามรายงาน accuracy/AUC ของ COPS-vs-NHANES** เพราะกลุ่มซ้อนกับยี่ห้อนาฬิกา 100%
- **ก่อน deploy หน้าเว็บ** ต้องเช็กว่า `docter.html` ยังชี้ `API_AWS`

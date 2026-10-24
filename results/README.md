# Results snapshot — daily cohort-reference baseline v1 (`real-20261024-full-v2`)

snapshot ของผลทั้งหมดที่ไม่ใช่ข้อมูลดิบ ณ 25 ต.ค. 2026: baseline v1, train/valid bundles, hourly features, รายงานของ run,
หลักฐาน clean (JSON) และผล backtest ทุก run รวม **test hold-out ที่เปิดแล้ว 1 ครั้ง**
คัดลอกจากโฟลเดอร์ทำงานที่ถูก gitignore (`backtest/outputs/`, `backtest/workspace/` และ worktree ของ run) โดยไม่แก้เนื้อหาไฟล์

> ผลทั้งหมดเป็น **ระยะห่างจากกลุ่มอ้างอิง (reference) COPS และ NHANES** ของตัวชี้วัดรายวัน 3 ตัว (ENMO, walking cadence,
> rest-tremor fraction) **ไม่ใช่** การวินิจฉัย ระดับความรุนแรง หรือความน่าจะเป็น และไม่รวมเป็นคะแนนเดียว
> cohort ตรงกับยี่ห้อนาฬิกา 100% (COPS = GENEActiv, NHANES = ActiGraph) ความต่างระหว่างกลุ่มอาจมาจากอุปกรณ์

## เริ่มอ่านจากไหน

- ผล test พร้อมคำอธิบาย: [`backtest/outputs/real-20261024-full-v2-test/README_TEST_RESULTS.md`](backtest/outputs/real-20261024-full-v2-test/README_TEST_RESULTS.md)
- สรุปผลและสูตร: [`../README_reference_baseline.md`](../README_reference_baseline.md) (หัวข้อ "Test hold-out — baseline v1")
- ขั้นตอนและกติกา train → valid → test: [`../backtest/RUNBOOK.md`](../backtest/RUNBOOK.md)

## มีอะไรบ้าง

| โฟลเดอร์ | ที่มา (path เดิม) | เนื้อหา |
| --- | --- | --- |
| `real-20261024-full-v2/models/` | `data/models/reference-v1/real-20261024-full-v2/` | `baseline.json` (SHA256 `e2a35a02…`), `test_plan_NOT_EXECUTED.json`, fit report, `checkpoints/` |
| `real-20261024-full-v2/daily/` | `data/daily/reference-v1/real-20261024-full-v2/` | daily bundles ของ train/valid (`daily.csv`, `hour_qc.csv`, `bundle.manifest.json`) |
| `real-20261024-full-v2/features/` | `data/features/reference-v1/real-20261024-full-v2/` | hourly features รายคน + `.manifest.json` |
| `real-20261024-full-v2/reports/` | `reports/reference_baseline/real-20261024-full-v2/` | `inventory.json` (raw บน S3 ที่ run ล็อกไว้), status, logs |
| `real-20261024-full-v2/clean-evidence/` | `data/clean/reference-runs/real-20261024-full-v2/` | เฉพาะ JSON: `*.qc.json`, `*.clean.manifest.json`, `*.started.json` |
| `backtest/outputs/` | `backtest/outputs/` | ทุก backtest run (real + synthetic), `baseline_registry.json`, `_test_opened/`, `_test_prepared/` |
| `backtest/configs/` | `backtest/workspace/configs/` | config ที่ใช้ปลดล็อก test (`real-full-v2-unlock.yaml`) |
| `backtest/test-workspace/real-20261024-full-v2/e2a35a02ff06/` | `backtest/workspace/real/test/…` | test bundle (`daily/test`), hourly, `progress.json`, `test_exclusions.json`, clean QC/manifest |

run ที่สำคัญใน `backtest/outputs/`: `real-full-v2-validate-v1` (validation + audit ที่ใช้เป็น readiness),
`real-20261024-full-v2-test` (test, `compared_once`), `real-full-v2-test-v1` (ความพยายามแรกที่ SHA พิมพ์ผิด ไม่ได้อ่าน test เลย),
`synthetic-*` (ข้อมูลจำลองสำหรับทดสอบซอฟต์แวร์ ไม่ใช่ผลจริง)

## ไม่ได้รวม

- clean parquet (~18 GB) และ raw (`.zip`, `.tar.bz2`, ~5 GB cache) — เกินขนาดที่ GitHub รับ ข้อมูลดิบโหลดได้จากต้นทาง (ด้านล่าง) หรือ S3 ของทีม
- ไฟล์ JSON/CSV ยังมี path ในเครื่องที่รัน (เช่น `D:\AWS\...`) ตามที่บันทึกตอนรัน ไม่มีผลกับการใช้งาน

## ตรวจความครบถ้วน

`SHA256SUMS.txt` ครอบคลุมทุกไฟล์ที่คัดลอกมา (ไม่รวม `README.md` นี้และตัวมันเอง):

```bash
cd results && sha256sum -c SHA256SUMS.txt
```

SHA ของ `real-20261024-full-v2/models/baseline.json` ต้องเป็น `e2a35a02ff067505c6594a3abb944d063fe4d3a31a6c7cad586af55631510bc9`

## กติกาการใช้ test

คน test ในผลนี้ (COPS 7, NHANES 11) **ถูกใช้แล้ว** ห้ามใช้ผล test ปรับหรือเลือกโมเดล config หรือ threshold รอบต่อไป
ถ้ารันคนชุดนี้กับ baseline ใหม่ ต้องรายงานว่าเป็น "test ที่ถูกใช้ซ้ำหลังเห็นผล" ไม่ใช่ held-out

## แหล่งข้อมูลและ license

- **COPS** — *Continuous Observation of Parkinsonian Symptoms*, OSF: <https://osf.io/5xvwn/> เผยแพร่ภายใต้
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) ไฟล์ในโฟลเดอร์นี้ที่มาจาก COPS เป็น **ผลที่ดัดแปลงแล้ว**
  (clean, สกัดตัวชี้วัด, รวมรายวัน, เทียบ reference) ไม่ได้รับรองโดยเจ้าของข้อมูล การนำไปใช้ต่อต้องให้เครดิต COPS ตาม CC BY 4.0
- **NHANES 2013–2014 PAX80_H** — CDC/NCHS <https://wwwn.cdc.gov/nchs/nhanes/> ข้อมูลสาธารณะ (public domain)
  ผลนี้ดัดแปลงแล้ว ไม่ได้รับรองโดย CDC/NCHS ห้ามพยายามระบุตัวบุคคลตามเงื่อนไขการใช้ข้อมูล NHANES

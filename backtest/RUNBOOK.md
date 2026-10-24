# Backtest runbook — train → valid → test (สถานะ ณ 24 ต.ค. 2026)

ผลทั้งหมดเป็น **ระยะห่างจากกลุ่มอ้างอิง COPS/NHANES** (`signed_distance`, `absolute_distance`, `standardized_deviation`)
ไม่ใช่ diagnosis/severity/probability/combined score และห้ามรายงาน accuracy/AUC ของ COPS-vs-NHANES (กลุ่ม = ยี่ห้อนาฬิกา 100%)

## 1. สถานะปัจจุบัน (audit จาก `real-checkpoint01-v1`, `real-checkpoint01-audit-v1`)

ข้อมูล: `real-20261023-v1 / checkpoint-01`, baseline `cb844f81…` (ตรวจจากสำเนาใน `backtest/workspace/real/`
ตอนเขียนเอกสารนี้ SSO token หมดอายุ จึงยังไม่ได้ยืนยันจำนวนคนที่ clean เสร็จเพิ่มบน S3 → ดูขั้น 1 ของ runbook)

### Train (reference)

| | COPS | NHANES |
| --- | --- | --- |
| คน train ใน splits | 35 | 106 |
| คนใน checkpoint-01 | 6 | 5 |
| ENMO / tremor reference | ok (6 คน, 28 วัน) | ok (5 คน, 30 วัน) |
| cadence reference | ok (5 คน, 18 วัน) | **insufficient_data** (5 คนมีวัน ok แต่มีแค่ 4 คนที่ ≥ 2 วัน < `min_participants` 5) |
| jackknife | ENMO pass 0.34; **tremor fail 0.539 > 0.5**; cadence not_evaluable (5→4) | not_evaluable ทุกตัว (5→4 < 5) |
| bootstrap CI / scale | 1.09 / 1.76 / 1.55 pass | **ENMO fail 2.69 > 2.0**; tremor 1.51 pass; cadence not_evaluable |

- tremor jackknife fail: ตัด COPS-41, COPS-5 **หรือ** COPS-9 ได้ shift เท่ากันเป๊ะ (0.539) = median ถ่วงน้ำหนักของ 6 คนอยู่บนขั้นระหว่างค่าของ 2 คน
  เป็นผลของ N น้อย ไม่ใช่ bug ของสูตร (audit `reference_formulas` 54/54 exact)
- NHANES ENMO bootstrap กว้างเพราะมี 5 คนที่ต่างกันมาก; sensitivity (memory-only) ยืนยันว่า `min_participants` 4 จะทำ LOO ได้ — **ห้ามแก้ config ด้วยเหตุนี้** ต้องเพิ่มคน
- LOO (`loo_train_rows.csv` 360 แถว): reference cohort ตัวเอง `excluded_train_subject=True` 100%, อีก cohort 0% → ตัดทุกวันของเจ้าตัวถูกต้อง
  แถว NHANES เทียบ NHANES เป็น `insufficient_data` ทั้งหมด (เหลือ 4 คน) ตามคาด
- `deviation_spread.csv`: reference NHANES ทำ jackknife ไม่ได้ (เหตุเดียวกัน); COPS ENMO ช่วงกลาง 0.95 z สูงสุด 3.06 z → standardized deviation ยังแกว่งมากเมื่อตัดคนอ้างอิง 1 คน
- เป้าหมาย: ทุก cohort × ตัวชี้วัด ต้องมี **≥ 6 คนที่มี ≥ 2 วัน ok** (LOO/jackknife) และในทางปฏิบัติมากกว่านั้นสำหรับ bootstrap
  — synthetic 8 คน/กลุ่ม กับ bootstrap 200 รอบ **ยัง fail** gate bootstrap ของ COPS cadence/tremor (`synthetic-flow-A`) ดังนั้นตัวเลข "6–7 คน" ใน README เป็นค่าขั้นต่ำที่มองโลกในแง่ดี

#### Pilot `aws-real-pilot-20261024-v1` (8 COPS + 8 NHANES, baseline `4b623bce…` = local py312 compatibility fit, ไม่ใช่ AWS)

- `real-pilot-20261024-v1`: **gate ความนิ่งผ่านครบ 12/12** (jackknife สูงสุด 0.36, bootstrap สูงสุด 1.70) NHANES cadence มี reference แล้ว (6 คน)
  audit 8 หมวด pass (`official_reports` not_evaluable เพราะรันด้วย `--baseline`), no-lookahead 0, device flag ยังขึ้น
- ยังไม่พร้อมเปิด test เพราะ valid 1+1 (`descriptive_only`/`unavailable`) อย่างเดียว และ pilot ไม่มี test plan
- `compare-pilot-local-py312-vs-ckpt01`: `new_version_more_train` (+COPS-13, COPS-16, NHANES-73746, 73781, 73899; ค่าเดิมไม่เปลี่ยน, preprocessing/policy เดิม)
  validate ซ้ำ (`…-validate`) audit pass → ยืนยันว่าเพิ่มคน 8+8 ก็พอให้ reference นิ่งตาม gate ที่ลงทะเบียนไว้

### Valid

- 1 COPS + 1 NHANES → `descriptive_only` ทุก cell (COPS cadence `unavailable`) ต้อง ≥ 3 คนที่ ok ต่อ cohort × ตัวชี้วัด จึงเป็น `distribution`
- splits มี COPS 7 / NHANES 22 คน → ต้องรัน `--stage validation` ให้ครบทุกคน (ขั้น 3)
- walk-forward: no_lookahead 0/180 (train), 0/33 (valid); previous_day ครบทุกขั้นยกเว้น `no_previous_day` (ครอบด้วย synthetic)
- device-confound: reference gap ENMO 0.663 vs tremor 0.210 (pooled scale) = 3.2 เท่า ≥ 2.0 → **flag ขึ้น** (ความต่าง ENMO อาจมาจากยี่ห้อ/gain)
  valid gap (n=1, descriptive) tremor 3.28 > ENMO 0.90; cadence not_evaluable
- audit 9 หมวด 0 failed: formulas 54/54, distances 660/660, null 66, previous_day 132, coverage 381, official 59 (33/33 แถวตรงทุกบิต), test_lock 3
- `overall=fail` มาจาก gate ความนิ่ง 2 ตัวเท่านั้น

### Test

- `blocked_no_test_features` และ **checkpoint baseline เปิด test ไม่ได้เลย**: `test_plan_NOT_EXECUTED.json` เขียนโดย
  `tools/run_real_reference.py --stage fit` (train ครบ) เท่านั้น พร้อม SHA ของ baseline ตัวนั้น
- เดิมไม่มีโค้ดสร้าง test bundle (`clean-one`/`run_subjects` ปฏิเสธ test โดยออกแบบ) → เพิ่ม `backtest/prepare_test.py` (ข้อ 2)
- `real-checkpoint01-readiness-v1` (รันซ้ำด้วยโค้ดใหม่ ผล gate เหมือนเดิม): **ไม่พร้อมเปิด test ทั้ง 5 cell** ที่ reference ok
  — ทุก cell ติด valid `descriptive_only`/`unavailable`; COPS tremor jackknife fail; NHANES ทุกตัว jackknife not_evaluable; NHANES ENMO bootstrap fail

### Track B (symptom, exploratory)

- `real-symptom-v1` (snapshot `20261024T085631Z` จาก `prepare_symptom`): train 31 คน (4 คนไม่มี legacy features), valid 0 คน
  (ทั้ง 7 คนไม่มี legacy features → `insufficient_valid`); candidate 2,244 ชม. ตรง README_signal_audit.md, จับคู่ features ได้ 1,579 (70%)
- CV 5 fold ตามคน: balanced accuracy 0.48 (CI 0.42–0.55) vs dummy 0.50, PR-AUC 0.276 (CI 0.17–0.41) vs prevalence 0.275
  → **ไม่มีสัญญาณเหนือ chance** ด้วย legacy features + logistic L2 (exploratory, COPS-only)
- การกัน leakage ในโค้ด: GroupKFold ตาม `subject_id` (assert ไม่มีคนซ้ำข้าม fold), impute median ของ fold + missing indicator (ไม่เติม 0,
  คอลัมน์ไม่มีค่าเลยถูกตัด), standardize/class weight จาก fold, ไม่ใช้ `exclude_columns`, ไม่อ่าน split test

## 2. สิ่งที่เพิ่ม/แก้ใน `backtest/` (ไม่แตะ `src/ tools/ config/ metadata/ tests/`)

| เรื่อง | ก่อน | ตอนนี้ |
| --- | --- | --- |
| `--pull` กับ test bundle | real mode materialize test bundle พร้อม `--pull` → อาจดึง test จาก S3 | หา test bundle ในเครื่องเท่านั้น ไม่ดึงจาก S3 |
| Track B อ่าน features | อ่าน `data/features/cops/*.csv` ทุกไฟล์ (รวม test 7 คน) แล้วค่อยตัด | เลือกไฟล์ตาม roster train/valid **ก่อน** อ่าน, ตัด label test ทันที, รายงาน match rate ของ day/hour |
| ความพร้อมก่อนเปิด test | มีแค่ล็อก 3 ชั้น | + **readiness gate**: ต้องมี validation run ของ baseline SHA นี้ที่ `overall ≠ fail`, audit ผ่าน, gates fingerprint เดิม และทุก cell ที่ reference ok มี jackknife+bootstrap **pass** และ valid `distribution` ไม่งั้น `locked_not_ready` |
| เปิด test ครั้งเดียว | marker เขียนหลัง compare สำเร็จ | marker เขียน **ก่อน** อ่านแถว test (ล้มกลางทางก็นับว่าเปิดแล้ว) |
| ตรวจ test bundle | แค่ split = test | + `config_hash`, `preprocessing_version` ตรง baseline, คนอยู่ใน roster test และใน test plan |
| full-run layout | path ผูก `checkpoints/{checkpoint}` | `real.checkpoint: null` = layout ของ `--stage fit` (ที่มี test plan) |
| สร้าง test bundle | ไม่มี | `python -m backtest.prepare_test` (ล็อกเดียวกับ test track + config/source/noise lock ต้องตรงที่ล็อกไว้) |
| synthetic flow | สร้าง baseline ใหม่ทุก run | `--synthetic-from <run>` ใช้ baseline ไฟล์เดิม → ทดสอบ validate → test → ปฏิเสธครั้งที่ 2 ได้จริง |
| registry | real เท่านั้น | real → `baseline_registry.json`, synthetic → `synthetic_registry.json` (ไม่ปนกัน) เพิ่ม `gates_fingerprint`, `audit_statuses`, `ready_cells` |

exit code ของ `backtest.run`: 1 = มี gate fail, 2 = ขอ test แต่ยังล็อก/ไม่พร้อม, 0 = อื่น ๆ

## 3. Runbook (PowerShell, รันจาก root ของ repo)

```powershell
$py = 'venv\Scripts\python'
aws sso login --profile minny
```

### ขั้น 1 — ดูว่ามีคนที่ clean + provenance ครบแล้วกี่คน

```powershell
& $py -X utf8 tools/run_real_reference.py --run-id real-20261023-v1 --stage status
# ดู completed_clean (n_qc_included ต่อ dataset × split) ใน reports/reference_baseline/real-20261023-v1/STATUS_REAL.json
```

### ขั้น 2 — ขยาย train (clean ต่อจากเดิม, ไม่เขียนทับ)

```powershell
& $py -X utf8 tools/run_real_reference.py --run-id real-20261023-v1 --stage train --workers 2
# ระหว่างทาง: snapshot ใหม่ (ชื่อใหม่เสมอ) เพื่อดูความนิ่งก่อนครบ
& $py -X utf8 tools/run_real_reference.py --run-id real-20261023-v1 --stage checkpoint --checkpoint-name checkpoint-02
# เมื่อ train ครบ: fit ตัวจริง -> data/models/reference-v1/<run>/baseline.json + test_plan_NOT_EXECUTED.json (locked SHA)
& $py -X utf8 tools/run_real_reference.py --run-id real-20261023-v1 --stage fit
```

- `initialize()` ปฏิเสธถ้า git HEAD/clean source/config/splits เปลี่ยนตั้งแต่เริ่ม run → รัน run ยาวใน `git worktree` แยก
  (เช่น `git worktree add --detach D:\AWS\Simianpal-refrun <commit>`) แล้วตั้ง `real.data_root` ของ backtest ไปที่ worktree นั้น
- clean evidence ของ `real-20261023-v1` อยู่บนเครื่อง `C:\working\a` เท่านั้น (S3 มีแค่ bundle/report) จึงต่อ run นั้นจากเครื่องอื่นไม่ได้
- `--workers` สูงสุด 2 และมี memory guard; รันซ้ำคำสั่งเดิมเพื่อ resume
- memory guard ดู **available commit** (ไม่ใช่ RAM ว่าง) ต่อคนต้องการ ~4.9–5.8 GiB + 1 GiB; เครื่องที่เปิดแอปอื่นเยอะ commit ว่างแค่ 4–9 GB
  → ใช้ `--workers 1` (หรือปิดแอป/เพิ่ม page file) ไม่งั้นทุกคน fail ทันที
- `--stage checkpoint` ก่อน fit ตัวเต็มจะสร้าง `data/models/reference-v1/<run>/checkpoints/` ทำให้ `fit_bundle` ปฏิเสธ
  ("Output model directory exists") → บันทึก SHA256 แล้วย้าย `checkpoints/` ออกชั่วคราว, `--stage fit`, ย้ายกลับและตรวจ SHA256
  (ทำแล้วกับ `real-20261024-full-v2`: baseline `e2a35a02…`, test plan lock ตรงกัน) หรือทำ checkpoint หลัง fit ตัวเต็มเท่านั้น
- ห้าม kill controller ระหว่างที่ worker ทำงาน: worker ที่ไม่มีแถว `failed` ใน `train_progress.json` จะ retry ใน run เดิมไม่ได้
  (`real-20261024-full-v1` ถูกทิ้งด้วยเหตุนี้ ใช้ `real-20261024-full-v2` แทน)

### ขั้น 3 — valid บน baseline ตัวเดียวกัน (ทุกคนใน split valid: COPS 7 / NHANES 22)

```powershell
& $py -X utf8 tools/run_real_reference.py --run-id real-20261023-v1 --stage validation --checkpoint-name checkpoint-02 --workers 2
# baseline ตัวเต็ม (หลัง --stage fit): ไม่ใส่ --checkpoint-name
& $py -X utf8 tools/run_real_reference.py --run-id real-20261023-v1 --stage validation --workers 2
```

checkpoint ใหม่เปลี่ยน `baseline_version` → `config_hash` ดังนั้น valid bundle ต้องมาจาก checkpoint เดียวกับ baseline เสมอ (bundle ของ checkpoint-01 ใช้กับ checkpoint-02 ไม่ได้)

### ขั้น 4 — backtest + audit ของ baseline นั้น (run ID ใหม่ทุกครั้ง)

แก้ `backtest/config/backtest_config.yaml` → `real`: `checkpoint` (`checkpoint-02` หรือ `null` สำหรับ baseline ตัวเต็ม) และ
`approved_baseline_sha256` = SHA ของ baseline.json ที่ส่ง **ห้ามแก้ `gates`** (pre-registered)

```powershell
$sha = (Get-FileHash data/models/reference-v1/real-20261023-v1/checkpoints/checkpoint-02/baseline.json -Algorithm SHA256).Hash.ToLower()
& $py -X utf8 -m backtest.run --mode real --pull --tracks stability loo walkforward valid audit symptom --run-id real-checkpoint02-v1
Get-Content backtest/outputs/real-checkpoint02-v1/summary.md -Encoding utf8   # ดูตาราง "ความพร้อมเปิด test"
```

- ใช้ path ใน config (ไม่ใช่ `--baseline`) เพื่อให้หมวด `official_reports` ถูก audit — run ที่ใช้ `--baseline` จะได้ official_reports
  `not_evaluable` และ **ไม่นับเป็นความพร้อมเปิด test**
- สิ่งที่ต้องเห็นก่อนไปต่อ: ทุก gate ไม่ `fail`, audit 9 หมวด `pass`, ทุก cell ที่ reference ok มี `ready=True`

### ขั้น 5 — เทียบกับ AWS (ถ้ามี SageMaker run)

```powershell
& $py tools/prepare_reference_aws.py --config config/sagemaker_reference_prebuilt.yaml --out reports/reference_baseline/aws-NEW-RUN/prebuilt_plan.json
# หลัง job เสร็จ (repo ไม่ submit job เอง):
& $py -X utf8 -m backtest.aws_compare --aws-artifact s3://<bucket>/.../output/model.tar.gz --aws-sha256 <sha ของไฟล์ที่ส่งมา> `
    --valid-bundle data/daily/reference-v1/real-20261023-v1/checkpoints/checkpoint-02/valid
```

`same_inputs_match` → ใช้ผล validation เดิม; `new_version_more_train` → validate + audit ใหม่ทันที (`<id>-validate`) ถ้า valid bundle มี config_hash ตรง;
`same_inputs_mismatch` / `different_inputs` → หยุดตรวจสาเหตุ AWS baseline มี SHA ต่างจาก test plan จึงเปิด test ไม่ได้ เว้นแต่ทีมตัดสินใจ fit/ล็อกใหม่อย่างชัดเจน

### ขั้น 6 — test (baseline ตัวเต็มเท่านั้น, ครั้งเดียวต่อ baseline)

ทำเมื่อขั้น 4 ของ **baseline ตัวเต็ม** (`real.checkpoint: null`) พร้อมแล้วเท่านั้น

```powershell
$sha = (Get-FileHash data/models/reference-v1/real-20261023-v1/baseline.json -Algorithm SHA256).Hash.ToLower()
# config ปลดล็อกแยกไฟล์ ต่างจากไฟล์หลักแค่ allow_test_split (gates ต้องเหมือนเดิม ไม่งั้น locked_not_ready)
(Get-Content backtest/config/backtest_config.yaml -Raw -Encoding utf8).Replace('allow_test_split: false', 'allow_test_split: true') |
    Out-File -Encoding utf8 backtest/config/backtest_config.test-unlock.yaml
$cfg = 'backtest/config/backtest_config.test-unlock.yaml'

# 6.1 ตรวจล็อกทั้งหมดโดยยังไม่แตะข้อมูล test
& $py -X utf8 -m backtest.prepare_test --config $cfg --unlock-test --approved-baseline-sha256 $sha --pull --dry-run
# 6.2 clean + extract test ด้วย config/source/noise lock ที่ล็อกไว้ (resume ได้, แบ่งเป็นชุดด้วย --ids)
& $py -X utf8 -m backtest.prepare_test --config $cfg --unlock-test --approved-baseline-sha256 $sha --pull
# 6.3 compare ครั้งเดียว (ไม่ fit)
& $py -X utf8 -m backtest.run --mode real --config $cfg --tracks test --unlock-test --approved-baseline-sha256 $sha `
    --test-bundle backtest/workspace/real/test/real-20261023-v1/$($sha.Substring(0,12))/daily/test --run-id real-test-$($sha.Substring(0,8))
# หลังเสร็จ: ลบไฟล์ unlock config ทิ้ง
Remove-Item backtest/config/backtest_config.test-unlock.yaml
```

- `prepare_test` ปฏิเสธถ้า: checkpoint baseline, ล็อกใด ๆ ไม่ผ่าน, ยังไม่พร้อม (readiness), test ถูก compare แล้ว,
  `pipeline_config.yaml`/`reference_config.yaml` ไม่ตรง SHA ใน test plan, noise lock ไม่ตรง config, clean source เปลี่ยน,
  หรือ extractor/clean config ปัจจุบันสร้าง `preprocessing_version` ไม่ตรง baseline
- เขียนเฉพาะ `backtest/workspace/real/test/<run>/<sha12>/` และ `backtest/outputs/_test_prepared/<sha>.json`; สร้าง bundle ครั้งเดียว
- เปิด test แล้ว (`outputs/_test_opened/<sha>.json`) จะเปิดซ้ำไม่ได้ แม้ run ล้มกลางทาง — การลบ marker คือการตัดสินใจของทีม ต้องบันทึกเหตุผล

### ขั้น 7 — Track B (ถ้าต้องการ)

```powershell
# ดึงเฉพาะ COPS train/valid: diary + QC + legacy features -> snapshot ใหม่ใน backtest/workspace/real/symptom/<UTC>/
& $py -X utf8 -m backtest.prepare_symptom
& $py -X utf8 -m backtest.run --mode real --tracks symptom --symptom-snapshot <dir ที่พิมพ์ออกมา> --run-id real-symptom-v1
```

ไม่ใช้ `tools/audit_model_readiness.py` เพราะดึง QC/diary ของทุก split รวม test; `source` ในผลรายงาน label match rate (day/hour ตาม README_signal_audit.md)

## 4. ตรวจซอฟต์แวร์ด้วย synthetic (ไม่ต้องต่อ S3)

```powershell
& $py -X utf8 -m pytest backtest/tests -q
& $py -X utf8 -m backtest.run --mode synthetic --run-id synthetic-flow-A --tracks stability loo walkforward valid audit
$sha = (Get-Content backtest/outputs/synthetic-flow-A/results.json -Raw -Encoding utf8 | ConvertFrom-Json).baseline_sha256
& $py -X utf8 -m backtest.run --mode synthetic --run-id synthetic-flow-B --synthetic-from synthetic-flow-A `
    --config <unlock config> --tracks test --unlock-test --approved-baseline-sha256 $sha
```

ด้วย gates ค่าเริ่มต้น B จะได้ `locked_not_ready` (bootstrap ของ synthetic 8 คน/กลุ่ม fail) ซึ่งเป็นพฤติกรรมที่ถูก;
เส้นทาง `compared_once` → ปฏิเสธครั้งที่ 2 ถูกทดสอบใน `backtest/tests/test_test_unlock.py` ด้วย gate ที่ลงทะเบียนไว้เหมือนกันทั้งสอง run

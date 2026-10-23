# Backtest — Daily cohort-reference baseline (+ symptom CV แบบสำรวจ)

โฟลเดอร์นี้แยกจากโปรเจกต์หลัก: **import `src.reference` / `src.features` / `tools.reference_baseline` แบบอ่านอย่างเดียว**
ไม่แก้ `src/ tools/ config/ metadata/ tests/` (ทุก run hash ไฟล์ในโฟลเดอร์เหล่านี้ก่อน/หลัง แล้วรายงานเป็น gate `protected_paths_unchanged`)
และเขียนเฉพาะ `backtest/workspace/` กับ `backtest/outputs/<run_id>/` ซึ่งถูก ignore จาก Git (มีข้อมูลรายคน ห้าม commit)

ผลทั้งหมดเป็น **ระยะห่างจากกลุ่มอ้างอิง COPS/NHANES** ไม่ใช่ diagnosis, severity หรือ probability
กลุ่ม = ยี่ห้อนาฬิกา 100% ความต่าง COPS vs NHANES จึงอาจมาจากอุปกรณ์ ห้ามรายงาน accuracy/AUC ของ COPS-vs-NHANES

## รัน

รันจาก root ของ repo เสมอ (ใช้ `-X utf8` บน Windows):

```powershell
venv\Scripts\python -X utf8 -m backtest.run --mode synthetic                 # ไม่ต้องต่อ S3
aws sso login --profile minny
venv\Scripts\python -X utf8 -m backtest.run --mode real --pull               # ดึง checkpoint จาก S3 ลง backtest/workspace/real/
venv\Scripts\python -X utf8 -m backtest.run --mode real --tracks stability valid --run-id real-NEW
venv\Scripts\python -X utf8 -m pytest backtest/tests -q
```

- `--tracks` เลือกจาก `stability loo walkforward valid audit symptom test` (ค่าเริ่มต้น = ทุกอย่างยกเว้น `test`)
- real mode อ่านเฉพาะ baseline ที่ SHA256 ตรง `real.approved_baseline_sha256` (baseline ที่ส่งไป `cb844f…`) ถ้าไม่ตรงจะหยุดก่อนอ่านไฟล์
- validate baseline อื่น (เช่นของ AWS): `--baseline <baseline.json> --approved-baseline-sha256 <sha> [--train-bundle ...] [--valid-bundle ...]`
- `--run-id` ต้องใหม่ทุกครั้ง ห้ามเขียนทับ run เดิม
- exit code 1 เมื่อมี gate ใด `fail`; `not_evaluable` ไม่ใช่ fail แต่ห้ามสรุปผลจาก cell นั้น
- real mode ใช้ไฟล์ใน `data/...` ของเครื่องก่อนถ้ามี ไม่มีจึงใช้/ดึงลง `backtest/workspace/real/<path เดิม>` (ไม่แตะ `data/` ของทีม)
  path ตั้งใน `config/backtest_config.yaml` → `real` (ค่าปัจจุบัน `real-20261023-v1` / `checkpoint-01`)

## ผลลัพธ์ใน `backtest/outputs/<run_id>/`

| ไฟล์ | เนื้อหา |
| --- | --- |
| `run_manifest.json` | เขียน **ก่อน** เปิดข้อมูล valid/test: gates + fingerprint, git commit/dirty, hash โค้ด backtest, เวอร์ชัน package, hash input (baseline, bundle manifest) |
| `results.json` | ผลทั้งหมด |
| `summary.md`, `dashboard.html` | สรุป + หน้า offline (inline SVG ไม่มี CDN) |
| `*.csv` | evaluability, gates, jackknife, bootstrap, sensitivity, deviation_spread, loo_train_rows, valid_rows, first_usable, within_subject_stability, known_answer |
| `analysis_VALID_<MODE>.json` | Analysis JSON schema 1.0.0 ที่ผ่าน `validate_output` ของโปรเจกต์ |

## สิ่งที่แต่ละระดับทำ

0. **Evaluability (ออกก่อนเสมอ)** — ต่อ cohort × ตัวชี้วัด: มีคน/วันพอสำหรับ full reference, LOO/jackknife, bootstrap, valid distribution (`min_valid_subjects_for_distribution`) และสถานะ test
1. **Stability บน train** — คำนวณใหม่ด้วย `src.reference.model.summarize()` จาก entries ใน `baseline.json` (ไม่ fit ใหม่ จึงไม่ต้องใช้ clean-run evidence)
   - jackknife: ตัดทีละคน → median ขยับกี่เท่าของ scale
   - subject bootstrap: สุ่มคนแบบใส่คืน **พร้อมตั้งชื่อใหม่ให้คนที่ซ้ำ** (ไม่งั้นจะรวมเป็นคนเดียวที่มีวันสองเท่า น้ำหนักผิด)
   - sensitivity ของ `min_participants`: override ใน memory ส่งตรงเข้า `summarize()` ติดป้าย `sensitivity_only_not_production` (แก้ cfg ทั้งก้อนไม่ได้เพราะ `config_hash` ของ daily จะไม่ตรง)
   - deviation spread: standardized deviation ของแต่ละวันแกว่งเท่าไรเมื่อตัดคนอ้างอิงอื่นทีละคน
   - train LOO: `build_analysis` เดิมของโปรเจกต์ (ตัดทุกวันของเจ้าตัวออกจาก reference)
2. **Walk-forward รายวัน** — reference ตรึงแล้ว ผลวัน t ไม่ควรขึ้นกับวันหลัง จึงตรวจ
   - no-lookahead: ผลวัน t เมื่อมีข้อมูลถึง t ต้องเท่ากับผลวัน t ตอนมีครบ (ระดับ analysis และ synthetic ระดับ hourly → daily)
   - กี่วันกว่าจะได้ `ok` ครั้งแรก, สัดส่วนวัน insufficient
   - ความนิ่งภายในคน: SD ของ standardized deviation, การเปลี่ยนจากวันต่อวัน (ไม่ข้ามช่องว่าง), `previous_day.delta`
3. **Valid** — SHA256 ของ baseline ก่อน/หลังต้องเท่ากัน, schema/semantic validation, distribution ต่อ (query cohort, reference)
   และ **device-confound diagnostic**: ระยะห่างระหว่าง cohort ในหน่วย pooled scale แยกตามชนิดสัญญาณ
   (`metric_signal_type`: ENMO = amplitude) ถ้า amplitude ห่างมากกว่าอย่างอื่น ≥ `device_flag_ratio` เท่า จะขึ้น flag (ไม่ใช่ fail)
   - synthetic มี **known-answer**: ฝัง shift 3 SD ใน ENMO ของคน valid คนละ cohort แล้วต้องกู้ได้ +3 SD เทียบกับค่าที่ไม่ได้ฝังของคนเดียวกัน
     รวมเคส zero_scale (NHANES tremor = 0 ทั้งหมด), ไม่มี walking window, วันหายกลางช่วง (`previous_day_missing`)
4. **Test (ล็อก)** — รันได้เมื่อครบทั้ง 3 อย่าง: `test.allow_test_split: true` ใน config, `--unlock-test`, และ
   `--approved-baseline-sha256` ที่ตรงกับไฟล์ baseline (real mode ต้องตรง `locked_baseline_sha256` ใน `test_plan_NOT_EXECUTED.json` ด้วย)
   compare อย่างเดียว ไม่ fit; เปิดได้ **ครั้งเดียวต่อ baseline** (`outputs/_test_opened/<sha>.json`) ตอนนี้ยังไม่มี test features → `blocked_no_test_features`
5. **Track B: symptom CV (สำรวจ ไม่ใช่เป้าหมายของ baseline)** — COPS เท่านั้น TremorScore 0 vs 1/2
   GroupKFold ตามคนบน train, impute ด้วย median ของ fold + missing indicator (ไม่เติม 0, คอลัมน์ที่ไม่มีค่าเลยถูกตัด),
   standardize/class weight จาก fold, logistic L2 (numpy ไม่ต้องติดตั้ง scikit-learn), เทียบ dummy, CI 95% แบบ bootstrap คน, ผลรายคน
   valid ประเมินเมื่อมี ≥ `min_valid_subjects` คนและครบสองคลาส ไม่อ่าน test; ไม่ใช้คอลัมน์ต้องห้ามใน `symptom.exclude_columns`
   real mode ต้องมี `data/features/cops/*.csv` และตั้ง `symptom.labels_path` (เช่น `diary_labels_REVIEW_ONLY.csv` จาก `tools/audit_model_readiness.py`)
   ต้องตรวจก่อนว่า day/hour ของ diary กับ features ตรงกัน (ดู README_signal_audit.md)

## Validation audit (track `audit`) — ใช้ baseline ที่ส่งไป ไม่ fit ใหม่

`backtest/runners/validation_audit.py` คำนวณซ้ำแบบอิสระ ไม่เรียก `summarize`/`compare_daily` (ใช้โค้ดเดิมแค่โหลดและสร้าง analysis ที่จะถูกตรวจ)
ผลอยู่ใน `validation_audit.json` และ gate `audit[<หมวด>]`:

| หมวด | ตรวจอะไร |
| --- | --- |
| `baseline_lock` | SHA = approved, ไม่ fit ระหว่าง validation, SHA หลัง audit เท่าเดิม |
| `hash_chain` | `config_hash`, `roster_hash`, `training_daily_hash` คำนวณใหม่ตรง และ `entries` ตรงกับแถว ok ใน train/daily.csv ทุกตัว |
| `reference_formulas` | median/quantiles/MAD/scale/status/n ด้วย `fractions.Fraction` (น้ำหนัก 1/(N·dᵢ) exact, tie ตามลำดับ subject/day) |
| `distances` | signed/abs/z ทุกแถว × 2 reference, valid ไม่มี `excluded_train_subject` |
| `null_handling` | ตารางกรณี + จำนวนที่เจอจริง (ไม่เจอ = `not_exercised` ครอบด้วย synthetic) |
| `previous_day` | ลำดับ 5 ขั้นของ `build_analysis`: day 0 → missing → current insufficient → previous insufficient → ok พร้อม day/value/delta |
| `coverage` | กฎ hour_qc, นาที/ชั่วโมงรายวัน, `metric_minutes`, `available = metric + missing`, status/reasons จากเกณฑ์, `coverage_report.json`, วันตาม `qc_valid_days` ครบ 3 metric |
| `official_reports` | เทียบ `analysis_VALIDATION_REAL.json` แถวต่อแถว + `validation_report.json`, `fit_report.json`, `valid_exclusions.json` |
| `test_lock` | test ยังล็อก ไม่มีแถว test |

## เทียบรุ่น AWS กับรุ่นในเครื่อง — `backtest/aws_compare.py`

เมื่อ SageMaker รันเสร็จ ส่ง artifact และ **SHA256 ของไฟล์ที่ส่งมา** (model.tar.gz หรือ baseline.json):

```powershell
venv\Scripts\python -X utf8 -m backtest.aws_compare --aws-artifact s3://<bucket>/.../output/model.tar.gz --aws-sha256 <sha> `
    [--aws-train-bundle <train bundle ที่ AWS ใช้>] [--valid-bundle <valid bundle ของ run เดียวกัน>]
```

- ตรวจ SHA ก่อน → snapshot ลง `backtest/workspace/aws/<run_id>/` → แตก tar เฉพาะ `baseline.json`/`fit_report.json` (ปฏิเสธ link, `..`, absolute path, ไฟล์ใหญ่เกิน)
- SHA ไฟล์และ `artifact_id` ของ AWS **ต่างจากรุ่นในเครื่องเสมอ** (created_utc, code_version) จึงเทียบเชิงเนื้อหา และแยก config 2 ระดับ:
  `policy_config_hash` (ตัด `baseline_version`/`paths`) ใช้จัดประเภท, `exact_config_hash` ใช้ตัดสินว่า valid bundle ใช้ร่วมได้ไหม
- ผล (`aws_comparison.json/.md`):

| ประเภท | ความหมาย | ต่อไป |
| --- | --- | --- |
| `same_inputs_match` | input/policy เดียวกัน summary เท่ากัน exact | ใช้ผล validation เดิม (`equivalent_to` ใน registry) |
| `same_inputs_mismatch` | (subject, day) เดิมแต่ค่าต่าง หรือ summary ต่าง (`mismatch_kind`: `value_changed` / `float_roundoff_only` / ...) | block ตรวจสาเหตุ |
| `new_version_more_train` | policy/preprocessing เดิม คน train เพิ่ม ค่าเดิมไม่เปลี่ยน | **รุ่นใหม่**: validate + audit ใหม่ทันที (run `<id>-validate`) ถ้า valid bundle มี preprocessing และ `config_hash` ตรง ไม่ตรง → `blocked_valid_bundle_config_hash_mismatch` |
| `different_inputs` | อย่างอื่น | ต้องมี input snapshot / valid bundle ของตัวเอง |

- `backtest/outputs/baseline_registry.json` เก็บต่อ baseline SHA: source, artifact_id, การเทียบ, validation runs และ `validation_audit_passed`
  (หมายถึง audit ความถูกต้องผ่าน ไม่ได้แปลว่า gate ความนิ่งผ่าน ดู `overall` ของแต่ละ run)
- `float_roundoff_only` ควรระวัง: `load_bundle` ใช้ parser ปกติของ pandas ซึ่งอ่าน CSV กลับไม่ตรงทุกบิต (checkpoint-01: 20/26 ค่า ต่างไม่เกิน 4.6e-15)
  ถ้า AWS ใช้ pandas คนละรุ่น (image มี 2.2.3) ค่า entries อาจต่างระดับนี้ได้

## Gates (pre-registered)

ค่าใน `gates` เป็น engineering defaults ที่ตั้งก่อนดูผล valid fingerprint ถูกเก็บใน `run_manifest.json`
ห้ามปรับหลังเห็นผล valid ถ้าทีมจะเปลี่ยน ให้ตกลงจาก train/synthetic แล้วเริ่ม run ใหม่

| gate | ความหมาย |
| --- | --- |
| `jackknife_median_shift_max_scale` 0.5 | ตัดคนอ้างอิง 1 คนแล้ว median ต้องขยับไม่เกิน 0.5 × scale |
| `bootstrap_median_ci_width_max_scale` 2.0 | ช่วง 95% ของ median จาก bootstrap คน ≤ 2 × scale (ประมาณต้องมี ≥6–7 คน) |
| `no_lookahead_mismatches_max` 0 | ไม่มีการใช้ข้อมูลอนาคต |
| `baseline_unchanged`, `protected_paths_unchanged` | baseline และโค้ด/config/split เดิมไม่ถูกแก้ |
| `known_answer_tolerance_sd` 0.75 | synthetic เท่านั้น |

## ผลรันจริงครั้งแรก (`real-checkpoint01-v1`, 24 ต.ค. 2026)

checkpoint-01 มี reference COPS 6 คน / NHANES 5 คน และ valid 1+1 คน (pilot) ดังนั้น
- NHANES ทุกตัวชี้วัดทำ jackknife ไม่ได้ (5 → 4 < ขั้นต่ำ 5) และ NHANES cadence ไม่มี reference เลย ตรงกับ README_reference_baseline.md
- gate ที่ fail: jackknife ของ COPS tremor (0.54 > 0.5) และ bootstrap CI ของ NHANES ENMO (2.69 > 2.0) — คือ reference ยังไม่นิ่งพอเพราะคนน้อย
- device flag: ระยะห่าง cohort ของ ENMO ใน reference มากกว่า tremor ≥ 2 เท่า
- valid เป็น `descriptive_only` (1 คน/กลุ่ม) ห้ามสรุปผล; Track B `blocked` (ไม่มี features/labels ในเครื่อง); test `blocked_no_test_features`

## Validation audit ของ baseline ที่ส่งไป (`real-checkpoint01-audit-v1`, 24 ต.ค. 2026)

baseline `cb844f…` (SHA เท่าเดิมหลังรัน ไม่มีการ fit) audit ผ่านครบ 9 หมวด:
- สูตร reference 54/54 ตรงแบบ exact, ระยะห่าง 660/660, coverage 381/381, previous_day 132/132
- `analysis_VALIDATION_REAL.json` ทางการ 33/33 แถวตรงทุกบิต, status/coverage/comparison counts และ fit_report ตรง
- null: daily insufficient 7, reference insufficient (NHANES cadence) 4, ok 48 ผ่านหมด; `zero_scale`, tremor = 0 และ `no_previous_day`
  (valid เริ่ม day 3 และ day 1) ไม่เกิดในข้อมูลจริง → ครอบด้วย synthetic test
- ข้อจำกัด: ไม่มี hourly features บน S3 จึงตรวจค่ารายวันย้อนจาก hourly ไม่ได้; valid มี 1+1 คน = ตรวจความถูกต้องของ pipeline ไม่ใช่ประสิทธิภาพ
- `overall=fail` มาจาก gate ความนิ่ง 2 ตัวเดิม (COPS tremor jackknife, NHANES ENMO bootstrap) ไม่ใช่จาก audit

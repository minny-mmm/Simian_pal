# ผล test hold-out — baseline v1 (`real-20261024-full-v2`)

> ผลทั้งหมดเป็น **ระยะห่างจากกลุ่มอ้างอิง (reference) COPS และ NHANES** ของตัวชี้วัดรายวัน 3 ตัว แยกกันทีละตัว
> **ไม่ใช่** การวินิจฉัย ระดับความรุนแรง ความน่าจะเป็น และไม่รวมเป็นคะแนนเดียว
> กลุ่ม (COPS/NHANES) ตรงกับยี่ห้อนาฬิกา 100% ความต่างระหว่างกลุ่มจึงอาจมาจากอุปกรณ์ ไม่ใช่โรค

## 1. สรุปสั้น

- เปิด test **ครั้งเดียว** แล้ว: `status = compared_once` เมื่อ 2026-10-24 16:16:20 UTC (23:16 น. เวลาไทย) ไม่มีการ fit ใหม่
- gate ทั้งหมดผ่าน (`overall = pass`) และ baseline ไม่เปลี่ยนก่อน/หลังรัน
- test มี 18 คน (COPS 7 + NHANES 11) / 101 วัน / 303 แถว (101 วัน × 3 ตัวชี้วัด): `ok` 289 แถว, `insufficient_data` 14 แถว (cadence ทั้งหมด)
- ตัวชี้วัดที่ต่างจาก reference มากที่สุดคือ **rest tremor fraction ของ COPS test เมื่อเทียบกับ NHANES reference**
  (มัธยฐานรายคน +2.39 และ 4 ใน 7 คนเกิน 2) แต่ค่าสูงมากมาจากแค่ 2 คน (COPS-22, COPS-53) ดูข้อ 7

## 2. ที่มาของผล

| รายการ | ค่า |
| --- | --- |
| run ID | `real-20261024-full-v2-test` (mode `real`, track `test`) |
| baseline SHA256 | `e2a35a02ff067505c6594a3abb944d063fe4d3a31a6c7cad586af55631510bc9` (fit ตัวเต็มของ train ใน `real-20261024-full-v2`) |
| readiness ก่อนเปิด | validation run `real-full-v2-validate-v1` → ready, ไม่มีเหตุผลค้าง |
| test bundle | `backtest/workspace/real/test/real-20261024-full-v2/e2a35a02ff06/daily/test` (bundle manifest SHA256 `0647710cd13ff486409d1a7772c1b9a083d129998e394a39b6cae2953399e1b3`) สร้างโดย `backtest.prepare_test` |
| code | git `6d2f82ef` (ไม่ dirty), ไฟล์ใน `src/ tools/ config/ metadata/ tests/` ไม่ถูกแก้ |
| config | `backtest/workspace/configs/real-full-v2-unlock.yaml` (ต่างจาก config หลักแค่การปลดล็อก test; gates fingerprint `5d704d5e…`) |

## 3. คนใน test

แผน test (`test_plan_NOT_EXECUTED.json` ที่ล็อกตอน fit) มี 29 คน: COPS 7, NHANES 22

| | จำนวน | รายชื่อ / เหตุผล |
| --- | ---: | --- |
| ใช้ได้ (included) | 18 | COPS ครบ 7/7, NHANES 11/22 |
| clean แล้วไม่ผ่าน QC | 1 | NHANES-78804 (`clean_qc_excluded`) |
| ไม่มี raw | 10 | NHANES-78876, 81465, 82182, 82291, 82756, 82998, 83122, 83146, 83381, 83687 (`raw_or_required_diary_missing`) |

ดังนั้น **NHANES test ได้มาแค่ครึ่งเดียวของแผน** สาเหตุ:

**1. ไม่มี raw บน S3 (10 คน)** — inventory ที่ run v2 บันทึกตอนเริ่ม (2026-10-24 08:58 UTC,
`reports/reference_baseline/real-20261024-full-v2/inventory.json`) พบไฟล์ `.tar.bz2` ใน `data/raw/nhanes/` แค่ **80 จาก 150 คน**
ไม่ใช่ปัญหาเฉพาะ test แต่ขาดทุก split (raw ไม่เคยถูกอัปโหลดตั้งแต่แรก ตรงกับที่บันทึกไว้ตั้งแต่ 23 ต.ค. ใน `README_reference_baseline.md`):

| split | มี raw | ไม่มี raw |
| --- | ---: | ---: |
| train | 60 | 46 |
| valid | 8 | 14 |
| test | 12 | 10 |

**2. ไม่ผ่าน QC (1 คน: NHANES-78804)** — clean ได้ แต่เหลือวันใช้ได้แค่ **1 วัน** ต่ำกว่าเกณฑ์ `clean.min_valid_days: 3`
(metadata ของ CDC ใน `nhanes_splits.csv` บอก 7 วัน แต่ pipeline นี้นับเฉพาะวันที่มีข้อมูล valid ≥ 10 ชม. หลังตัด nonwear/flag อื่น
ซึ่งเข้มกว่า และใช้เกณฑ์เดียวกันทั้งสอง dataset)

**เติมคนได้ไหม**
- baseline นี้ **ไม่ได้**: inventory ถูกล็อก การดึง raw ใหม่มาเพิ่มจะทำให้ input ไม่ตรงกับ baseline และ test ของ SHA นี้เปิดไปแล้ว
- baseline รุ่นถัดไป: ต้องอัปโหลด NHANES raw ที่ขาด 70 คนขึ้น S3 ก่อน (จะเพิ่ม train 60 → สูงสุด 106 และ valid 8 → 22 ด้วย)
  แต่ test 12 คนที่เห็นผลแล้วต้องรายงานเป็น reused test มีแค่ 10 คนที่ยังไม่เคยเปิดที่นับเป็น held-out จริงได้ ซึ่งน้อยเกินสำหรับสรุประดับกลุ่ม

## 4. วิธีอ่านตัวเลข

ต่อวัน ต่อตัวชี้วัด และต่อ reference (COPS หรือ NHANES):

```text
signed_distance        = ค่ารายวัน − median ของ reference          (หน่วยจริง)
scale                  = 1.4826 × weighted MAD ของ reference
standardized_deviation = signed_distance / scale                   (ไม่มีหน่วย; ตารางด้านล่างใช้ค่านี้)
```

- **0** = เท่ากับ median ของ reference, **บวก** = สูงกว่า, **ลบ** = ต่ำกว่า
- **|ค่า| > 2** = ห่างจาก median มากกว่า 2 เท่าของ scale แบบ robust ใช้แค่เป็นตัวบอกว่า "ห่างมาก" ไม่ใช่ p-value หรือความน่าจะเป็น
- **ระดับวัน** = ทุกวันรวมกัน (คนที่มีหลายวันมีน้ำหนักมากกว่า); **ระดับคน** = มัธยฐานของแต่ละคนก่อน แล้วสรุปข้ามคน (ทุกคนน้ำหนักเท่ากัน)
- `insufficient_data` = ข้อมูลวันนั้นไม่พอ (เช่น เดินน้อยกว่า 5 นาที) ค่าเป็น null **ไม่ใช่ 0** และไม่ถูกนับในสถิติ
- reference ใช้คน train อย่างเดียว คน test ไม่อยู่ใน reference

## 5. Reference ที่ใช้เทียบ (train)

| ตัวชี้วัด | หน่วย | COPS: คน / วัน | COPS median (scale) | NHANES: คน / วัน | NHANES median (scale) |
| --- | --- | ---: | --- | ---: | --- |
| ENMO (`enmo_hourly_median_mean_g`) | g | 31 / 146 | 0.0208 (0.0094) | 58 / 357 | 0.0266 (0.0099) |
| rest tremor fraction (`rest_tremor_window_fraction`) | สัดส่วน 0–1 | 31 / 146 | 0.0487 (0.0481) | 58 / 357 | 0.0429 (0.0258) |
| walking cadence (`walking_cadence_hourly_median_mean_hz`) | steps/s | 24 / 100 | 1.795 (0.184) | 50 / 274 | 1.786 (0.183) |

scale ของ tremor ใน NHANES แคบกว่า COPS ประมาณครึ่งหนึ่ง ดังนั้นค่ารายวันเดียวกันจะได้ standardized_deviation เทียบ NHANES สูงกว่าเกือบ 2 เท่า

## 6. ผลรวมต่อกลุ่ม

มัธยฐาน standardized_deviation: ระดับวัน [q25, q75] / ระดับคน — และสัดส่วนคนที่ |มัธยฐานรายคน| > 2

| test | ตัวชี้วัด | วัน ok | เทียบ COPS ref | คน >2 | เทียบ NHANES ref | คน >2 |
| --- | --- | ---: | --- | ---: | --- | ---: |
| COPS (7 คน) | ENMO | 29 | +0.36 [+0.03, +1.15] / +0.30 | 0/7 | −0.24 [−0.55, +0.50] / −0.30 | 0/7 |
| | rest tremor | 29 | +0.77 [−0.03, +1.69] / +1.16 | 2/7 | +1.66 [+0.17, +3.37] / +2.39 | 4/7 |
| | cadence | 27 (2 insufficient) | +0.51 [+0.20, +0.97] / +0.51 | 1/7 | +0.56 [+0.25, +1.03] / +0.56 | 1/7 |
| NHANES (11 คน) | ENMO | 72 | +1.26 [+0.17, +1.97] / +1.46 | 3/11 | +0.61 [−0.42, +1.28] / +0.80 | 2/11 |
| | rest tremor | 72 | −0.02 [−0.36, +0.31] / −0.01 | 1/11 | +0.19 [−0.45, +0.81] / +0.21 | 1/11 |
| | cadence | 60 (12 insufficient) | +0.26 [−0.50, +0.95] / +0.20 | 1/11 | +0.31 [−0.46, +1.00] / +0.25 | 1/11 |

## 7. ผลรายคน

มัธยฐาน standardized_deviation ของแต่ละคน: `เทียบ COPS ref / เทียบ NHANES ref`

| คน | วัน | ENMO | rest tremor | cadence | วัน cadence insufficient |
| --- | ---: | --- | --- | --- | ---: |
| COPS-12 | 4 | +0.30 / −0.30 | −0.17 / −0.09 | −0.55 / −0.51 | 1 |
| COPS-22 | 3 | +0.65 / +0.03 | **+7.28 / +13.81** | +0.40 / +0.45 | 0 |
| COPS-30 | 3 | +0.29 / −0.31 | +1.16 / **+2.39** | +0.71 / +0.76 | 0 |
| COPS-31 | 5 | +0.02 / −0.56 | +0.44 / +1.05 | −0.04 / +0.01 | 0 |
| COPS-38 | 6 | +0.19 / −0.40 | −0.13 / −0.01 | +0.51 / +0.56 | 1 |
| COPS-53 | 3 | +1.16 / +0.52 | **+12.35 / +23.28** | **+2.58 / +2.65** | 0 |
| COPS-65 | 5 | +1.52 / +0.86 | +1.21 / **+2.49** | +0.82 / +0.88 | 0 |
| NHANES-73581 | 5 | +0.55 / −0.06 | +0.19 / +0.58 | **+2.22 / +2.29** | 1 |
| NHANES-73622 | 7 | **+2.06** / +1.37 | −0.33 / −0.39 | −0.92 / −0.88 | 1 |
| NHANES-74016 | 7 | +1.48 / +0.81 | −0.45 / −0.62 | +0.87 / +0.92 | 0 |
| NHANES-74302 | 7 | +1.49 / +0.82 | +0.14 / +0.48 | −1.67 / −1.64 | 1 |
| NHANES-75012 | 3 | **+4.04 / +3.25** | **+3.75 / +7.23** | −0.43 / −0.39 | 0 |
| NHANES-76023 | 7 | +0.32 / −0.28 | −0.01 / +0.21 | −0.24 / −0.20 | 3 |
| NHANES-76560 | 7 | +0.00 / −0.58 | +0.68 / +1.50 | +1.47 / +1.53 | 0 |
| NHANES-77063 | 8 | +0.98 / +0.35 | −0.03 / +0.17 | +0.21 / +0.26 | 2 |
| NHANES-77551 | 7 | +1.46 / +0.80 | −0.32 / −0.37 | +0.18 / +0.22 | 2 |
| NHANES-78583 | 7 | −0.13 / −0.71 | −0.36 / −0.44 | +0.20 / +0.25 | 0 |
| NHANES-78674 | 7 | **+2.75 / +2.02** | +0.30 / +0.79 | +1.89 / +1.95 | 2 |

ตัวหนา = |ค่า| > 2 รวม COPS 29 วัน, NHANES 72 วัน

## 8. ข้อสังเกต (เชิงพรรณนาเท่านั้น)

- **Rest tremor**: COPS test อยู่สูงกว่าทั้งสอง reference และห่างจาก NHANES reference มากกว่า (ส่วนหนึ่งเพราะ scale ของ NHANES แคบกว่า ดูข้อ 5)
  ค่าสูงมากมาจาก COPS-22 และ COPS-53 เท่านั้น อีก 5 คนอยู่ระหว่าง −0.2 ถึง +2.5 ส่วน NHANES test อยู่ใกล้ 0 ทั้งสอง reference ยกเว้น NHANES-75012 (มีแค่ 3 วัน)
- **ENMO**: COPS test อยู่ใกล้ทั้งสอง reference (ไม่มีใครเกิน 2) ส่วน NHANES test **อยู่สูงกว่า NHANES reference ของตัวเอง** (ระดับคน +0.80, 2/11 คนเกิน 2)
  ซึ่งไม่ควรเกิดถ้าคน test มาจากประชากรเดียวกับ train อาจเกิดจาก n เล็กหรือความต่างระหว่างคนจริง บันทึกเป็นข้อสังเกตเท่านั้น
- **Cadence**: ทั้งสองกลุ่มอยู่ใกล้ reference (มัธยฐานรายคน +0.2 ถึง +0.6); reference ของ COPS และ NHANES แทบเท่ากัน (1.795 vs 1.786 steps/s)
  cadence เป็น `insufficient_data` 14 จาก 101 วัน (เดินไม่พอ) — NHANES 12/72, COPS 2/29
- ห้ามแปลความต่าง COPS กับ NHANES ข้างบนว่าเป็นผลของโรค เพราะยี่ห้อนาฬิกาต่างกันทุกคน

## 9. ข้อจำกัด และกฎหลังเปิด test

- n เล็ก: COPS 7 คน (3–6 วัน/คน), NHANES 11 คน (3–8 วัน/คน) ค่าระดับกลุ่มจึงเปลี่ยนได้มากถ้าคนเปลี่ยน 1–2 คน
- NHANES test ได้แค่ 11 จาก 22 คนตามแผน (ข้อ 3) จึงอาจไม่เป็นตัวแทนของ split test ทั้งหมด
- test ไม่มี gate pass/fail ของตัวระยะห่างเอง `overall = pass` หมายถึงผ่านการตรวจ schema / baseline ไม่เปลี่ยน / ไม่มีการแก้โค้ดที่ล็อกไว้เท่านั้น
- **คน test ชุดนี้ถูกใช้แล้ว**: ห้ามใช้ผลนี้ปรับหรือเลือกโมเดล config หรือ threshold รอบต่อไป (ใช้ train/valid, LOO, walk-forward แทน)
  ถ้าจะรันคนชุดนี้กับ baseline ใหม่ ต้องรายงานว่าเป็น "test ที่ถูกใช้ซ้ำหลังเห็นผล" ไม่ใช่ held-out
- baseline นี้เปิด test ซ้ำไม่ได้ (`backtest/outputs/_test_opened/e2a35a02….json`)

## 10. ไฟล์ในโฟลเดอร์นี้

| ไฟล์ | เนื้อหา |
| --- | --- |
| `README_TEST_RESULTS.md` | ไฟล์นี้ (เขียนเพิ่มทีหลัง ไม่ได้มาจาก `backtest.run`) |
| `summary.md` | สรุปอัตโนมัติ: gates, evaluability, สถานะ test |
| `results.json` | ผลเต็ม: `tracks.test.distribution` (ตารางข้อ 6), readiness, baseline SHA ก่อน/หลัง |
| `analysis_TEST_REAL.json` | 303 แถวรายวัน/ตัวชี้วัด: ค่า, coverage, QC, ระยะห่างจากทั้งสอง reference, `previous_day` (ตารางข้อ 5 และ 7 มาจากไฟล์นี้) |
| `gates.csv` | ผล gate 3 ตัว |
| `evaluability.csv` | reference/valid ของแต่ละ cohort × ตัวชี้วัด ประเมินได้ระดับไหน |
| `dashboard.html` | dashboard เปิดแบบ offline |
| `run_manifest.json` | config, gates fingerprint, hash ของโค้ด |

ไฟล์ที่เกี่ยวข้องนอกโฟลเดอร์: `backtest/outputs/_test_opened/<sha>.json` (บันทึกการเปิด test), `backtest/outputs/_test_prepared/<sha>.json`
(บันทึกการเตรียม bundle) และ `backtest/workspace/real/test/real-20261024-full-v2/e2a35a02ff06/` (`progress.json`, `test_exclusions.json`, clean/hourly/daily)
ทั้งหมดถูก gitignore และมีอยู่เฉพาะเครื่องที่รัน — สรุปที่ commit แล้วอยู่ใน `README_reference_baseline.md` หัวข้อ "Test hold-out — baseline v1"

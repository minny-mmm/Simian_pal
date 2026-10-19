# ตรวจต้นทางและกำหนดคำถาม

อัปเดตหลังต่ออายุ SSO: ตรวจ S3 สำเร็จแล้ว พบ clean/QC มากกว่า snapshot ในเครื่อง และ QC ของ NHANES บน S3 มี noise target ต่อแกนปัจจุบัน ดู [แผนใช้ข้อมูลต่อและผลตรวจล่าสุด](README_model_plan.md) ตัวเลขและสถานะ SSO ด้านล่างเป็นผล local snapshot ก่อนต่ออายุ ไม่ใช่สถานะล่าสุดบน S3

รันจาก root ของโปรเจกต์:

```powershell
.venv/Scripts/python.exe tools/audit_sources.py
.venv/Scripts/python.exe -m pytest tests/test_audit_sources.py -q
```

คำถามหลักที่กำหนดสำหรับงานระยะนี้คือ **จำแนกกลุ่ม COPS PD กับกลุ่มควบคุมที่คัดจาก NHANES ในระดับผู้เข้าร่วม** เพื่อสำรวจความเป็นไปได้ ยังสรุปความสามารถแยกโรคข้ามอุปกรณ์ไม่ได้ ส่วนคำถามรองคือคะแนนอาการสั่นรายชั่วโมงใน COPS ซึ่งต้องดึง TremorScore และตรวจการจับคู่เวลาเพิ่มเติม ไม่ใช้ label PD ที่คงที่ทั้งคนแทนคะแนนอาการรายชั่วโมง

ตัวตรวจอ่าน roster โดยตรงก่อน merge เพื่อไม่กลบคนที่ไม่มี split หรือ ID ซ้ำ ตรวจ metadata ใน QC JSON เทียบ roster/config และ qc_summary, เกณฑ์รวมวัน/ชั่วโมง, noise target และ feature label/split/day พร้อมบันทึก SHA256 ของ input ที่อ่าน จำนวนชั่วโมงอ้างอิง `hours_valid` หลังตัดวัน/คนที่ไม่ผ่าน ไม่รวม `valid_h` ของวันสั้นหรือของคนที่ถูกตัด และไม่นับแถว feature เป็นชั่วโมงเต็ม

ไฟล์ผลอยู่ใน `reports/source_audit/` และถูก ignore ตามกติกา reports ของโปรเจกต์:

| ไฟล์ | ใช้ทำอะไร |
| --- | --- |
| `report.md` | สรุปและข้อจำกัดของหลักฐานในเครื่อง |
| `participants.csv` | รายชื่อ, label basis, split, fs และสถานะ QC/ไฟล์รายคน |
| `coverage.csv` | จำนวนคนและชั่วโมงแยก label, dataset, อุปกรณ์, split มีช่อง 0 สำหรับกลุ่มที่ไม่มี |
| `issues.csv` | รายการ metadata ไม่ตรง, ขาดไฟล์, ผลเก่า และชั่วโมงซ้ำ |
| `input_manifest.json` | hash ของ config/roster/QC/feature ที่ใช้ตรวจ |
| `baseline_features_REVIEW_ONLY.csv` | feature เดิมที่แยกสองคอลัมน์น่าสงสัยออกแล้ว ใช้ตรวจเท่านั้น |
| `suspect_features_REVIEW_ONLY.csv` | เก็บ still_frac และ rest_tremor_ratio พร้อม source_row เพื่อ join กลับอย่างถูกต้อง |
| `duplicate_feature_hours.csv` | เก็บทุกแถวที่ชนคีย์ dataset/subject/day/hour โดยไม่เฉลี่ยกลบข้อผิดพลาด |
| `experiment_plan.json` | รายชื่อ feature และแผน baseline / เพิ่มทีละตัว / เพิ่มทั้งคู่ |

ผลตรวจ local snapshot วันที่ 19 ต.ค. 2026:

| กลุ่ม / อุปกรณ์ | train | valid | test | QC ในเครื่อง | ผ่าน QC | ชั่วโมงตาม QC ของคนที่ผ่าน |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| COPS PD / GENEActiv | 35 | 7 | 7 | 30 | 27 | 2,003.27 |
| NHANES selected control / ActiGraph GT3X+ | 106 | 22 | 22 | 2 | 2 | 138.96 |

ชื่ออุปกรณ์และ label เป็นการกำหนดตาม cohort จากเอกสาร/โค้ดคัดเลือก ไม่ใช่ผลตรวจ serial หรือวินิจฉัยรายคนใหม่ ไม่พบกลุ่มควบคุมใน COPS และไม่พบ PD ในรายชื่อ NHANES ที่เลือก ทั้ง train/valid/test จึงมีปัญหา dataset/device ซ้อนกับโรค การลองทาย dataset เป็น diagnostic ทำได้ แต่แยกสาเหตุของคะแนนที่ดีไม่ได้เมื่อ target ตรงกันทั้งหมด

มี QC JSON 32 จาก 199 คน อีก 167 คนยังไม่มีหลักฐาน QC ในเครื่อง ไม่พบ metadata mismatch ในไฟล์ที่ตรวจได้ แต่ยังไม่มี clean parquet ในเครื่องแม้แต่คนเดียว จึงยังตรวจ sampling rate จาก timestamp และ valid/reason ของสัญญาณจริงไม่ได้ QC ของ NHANES ทั้งสองคนขาด noise target ต่อแกนตาม config ปัจจุบัน จึงนับ 138.96 ชั่วโมงนั้นเป็นผลเก่าสำหรับตรวจเท่านั้น จำนวนชั่วโมงของคนที่ยังไม่มี QC คือ **ไม่ทราบ** ไม่ใช่ไม่มีข้อมูล

feature เดิมมี 199 แถวจาก 2 คน พบ 27 คีย์ชั่วโมงซ้ำ รวม 54 แถวที่เกี่ยวข้อง และไม่มี manifest ยืนยันเวอร์ชัน feature/clean จึงส่งออกเป็น REVIEW_ONLY ทั้งสองชุด ยังไม่เทรนโมเดลหรือรายงานคะแนน ablation

การอ่าน inventory บน S3 ใน session นี้ติด AWS SSO token หมดอายุและ refresh ไม่สำเร็จ จึงยังยืนยันจำนวนบน S3 ไม่ได้ หลังเจ้าของบัญชีต่ออายุด้วย `aws sso login --profile minny` ตรวจต่อด้วย `python tools/s3_pull.py list` แล้วดึง QC/clean ที่ต้องใช้มาเทียบ ห้ามสรุปว่าไฟล์ที่ไม่มีในเครื่องไม่มีบน S3

ก่อนรัน ablation ต้องแก้/สร้าง hourly feature ใหม่จาก clean ที่ตรง config พร้อม hash ของโค้ด/config/clean และแก้ชั่วโมงซ้ำ ใช้ split ตามคนเหมือนกันทุกชุด เรียนรู้ imputation/scaling/threshold จาก train เท่านั้น เปรียบเทียบ validation AUROC/balanced accuracy ระดับคน พร้อมช่วงความเชื่อมั่นจาก bootstrap ตามคน และล็อกวิธีก่อนเปิด test ฟีเจอร์ baseline อื่นยังอาจแยกอุปกรณ์ได้ด้วย ไม่ถือว่าผ่านการรับรองเพราะเอาสองคอลัมน์ออกแล้ว

หากจะสรุปเรื่องโรคต้องเพิ่มข้อมูลที่มี PD และกลุ่มควบคุมใน dataset/อุปกรณ์เดียวกัน การใช้ยาอย่างเดียวเป็น label ของ PD ต้องตรวจยืนยัน ไม่ถือเป็น ground truth อัตโนมัติ ส่วนงานคะแนนอาการรายชั่วโมงต้องตรวจ diary coverage และการเหลื่อมเวลาก่อน และประเมินแยกจากงานจำแนก PD

# ตรวจสัญญาณจริงรายชั่วโมงจาก train

เครื่องมือ `tools/audit_signal_hours.py` อ่าน clean parquet จริงของ COPS train ที่มี candidate TremorScore จาก snapshot ของ readiness ไม่ใช้ valid/test และไม่แก้ข้อมูลบน S3

## ผลตรวจจริง 19 ต.ค. 2026

สแกน 31 คน รวม 908,774,849 samples ใน 5,077 clock hours พบ candidate diary 2,244 ชั่วโมง จับคู่ Day/Time/ชื่อไฟล์ข้อมือได้ครบ ไม่พบ timestamp ซ้ำ หลุดกริด 50 Hz, วันไม่ตรง, valid ที่มี XYZ ไม่ finite หรือ valid/reason/visit ขัดกัน ยอด valid รวมตรงกับ QC ทั้ง 31 คนภายในความคลาดเคลื่อนการปัดสองตำแหน่ง

กฎที่บันทึกก่อน scan เลือกขั้นต่ำ **20 นาที valid ต่อชั่วโมง**: เก็บ 2,040/2,244 ชั่วโมง (90.91%) และยังมีผู้เข้าร่วมครบ 31 คน รวม valid samples 1,812.53 ชั่วโมง หากใช้ 30 นาทีจะเหลือ 1,963 ชั่วโมง (87.48%) ต่ำกว่าเป้าหมายคงชั่วโมง 90% จึงไม่ได้เลือก

| TremorScore | Candidate hours | ผ่านขั้นต่ำ | คัดออก |
| --- | ---: | ---: | ---: |
| 0 | 1,632 | 1,470 | 162 |
| 1 | 492 | 458 | 34 |
| 2 | 120 | 112 | 8 |

ทั้งหมด 204 ชั่วโมงถูกคัดออกเพราะ valid duration ต่ำกว่าเกณฑ์ ในจำนวนนี้ **132 ชั่วโมงไม่มี valid sample เลยแม้ diary เข้า candidate** พบธง sleep/ISM/nonwear ในกลุ่มที่ถูกตัด (ธงซ้อนกันได้) เป็นประเด็น QC–diary ที่ควรตรวจต่อ ไม่ใช่หลักฐานว่าสัญญาณหายหรือผู้เข้าร่วมไม่มีอาการ และยังไม่มีการผ่อน QC เพื่อเอาชั่วโมงเหล่านี้กลับมา

ชั่วโมงที่เก็บมีหน้าต่าง 10 วินาทีแบบเต็มอย่างน้อย 122 หน้าต่างต่อชั่วโมง รวมเวลาจากหน้าต่างเต็ม 1,799.92 ชั่วโมง ซึ่งน้อยกว่า valid sample duration เพราะเศษช่วง/หน้าต่างที่ไม่ครบถูกตัดออก จึงควรใช้รายชื่อใน `included_hours.csv` เป็นจุดเริ่มสกัด feature และใช้หน้าต่างต่อเนื่องจริง ไม่ต่อ sample ข้ามช่องว่าง

ตรวจกราฟตัวอย่างทั้งสามคะแนน, กรณี valid=0 และกรณีใกล้เกณฑ์แล้ว กราฟมีทั้งช่วงเคลื่อนไหว ช่วงสงบ และ transient; PSD ของช่วงสั้นไม่ยืนยันอาการทั้งชั่วโมง จึงยังใช้ diary เป็นคะแนนระดับชั่วโมง ไม่ตีความเป็น label ทุกวินาที ดูผลและกราฟใน `reports/signal_audit/report.md`

เกณฑ์นี้ตรึงจาก train สำหรับเตรียม development cohort ก่อนเทรน เมื่อประเมิน valid/test ต้องใช้เกณฑ์เดิม หากสำรวจหรือเลือกเกณฑ์ใหม่ระหว่าง cross-validation ให้คำนวณใน training fold เท่านั้น

```powershell
.venv/Scripts/python.exe tools/audit_signal_hours.py
.venv/Scripts/python.exe tools/audit_signal_hours.py --refresh-plots
.venv/Scripts/python.exe -m pytest tests/test_signal_hours.py -q
```

ต้องรัน `tools/audit_model_readiness.py` มาก่อน ค่าเริ่มต้นเลือก snapshot ล่าสุดใน `reports/model_readiness/snapshots/` หรือระบุ `--snapshot PATH` ได้ ต้องมี matplotlib ตาม requirements การรันครั้งแรกโหลด train ประมาณ 4.25 GB และเก็บ cache ใน `reports/signal_audit/cache/` รอบถัดไปเช็กขนาด/ETag/VersionId ก่อนใช้ cache และปฏิเสธ QC บน S3 ที่เปลี่ยนจาก snapshot

การจับคู่เวลาใช้ Day N / Time H hr เป็นช่วง `[N*86400+(H-1)*3600, N*86400+H*3600)` ตรวจซ้ำกับชื่อไฟล์ CSV/ZIP ของข้อมือที่ระบุใน split และคอลัมน์ time_s/day/visit ใน parquet ไม่ถือว่า row group คือวันหรือชั่วโมง ไม่มีการชดเชยเวลาอัตโนมัติหรือเลือก offset ที่ทำให้คะแนนโมเดลดีขึ้น การตรงกันนี้เป็นหลักฐานเวลาในระบบ study-time ไม่ยืนยันว่าเวลา diary กับนาฬิกาจริงไม่คลาดเคลื่อน

ต่อชั่วโมงตรวจ timestamp ต้องอยู่บน grid และห่าง 1/fs วินาที ไม่มีเวลาซ้ำ/ย้อนกลับ, day ต้องตรงกับ time_s, valid ต้องไม่ว่างและมี XYZ finite, reason ต้องสอดคล้อง valid, valid samples ต้องอยู่ outpatient พร้อมวัดจำนวนนาที valid, ช่วงต่อเนื่องยาวที่สุด, หน้าต่าง 10 วินาทีที่ครบทุก sample และสถิติขนาดสัญญาณ ใช้ผลทุกชั่วโมงในไฟล์เทียบยอด valid รวมกับ QC โดยยอมให้ต่างตามการปัดสองตำแหน่ง

กฎเลือกขั้นต่ำถูกบันทึกก่อน scan ใน `threshold_policy_pre_scan.json`: เลือกค่ามากที่สุดจาก 5/10/15/20/30 นาที ที่ยังเก็บอย่างน้อย 90% ของ candidate hours ที่โครงสร้างตรงกัน และ 90% ของผู้เข้าร่วมที่มีชั่วโมงเหล่านั้น หากไม่มีค่าผ่านใช้ 5 นาทีเป็น fallback พร้อมระบุว่าไม่ผ่านเป้าหมาย กฎนี้ใช้เฉพาะ coverage ใน train ไม่รับ label หรือ prediction เป็น input เป็นเกณฑ์ความครอบคลุมทางวิศวกรรม ยังไม่ใช่ cutoff ที่ยืนยันทางคลินิก และอาจต้องตรวจความไวต่อผลโมเดลภายใต้ train CV ภายหลัง

ผลอยู่ใน `reports/signal_audit/`:

| ไฟล์ | ความหมาย |
| --- | --- |
| `report.md` | ผลตรวจและกราฟรวม พร้อมกราฟสัญญาณตัวอย่าง |
| `all_train_diary_hours.csv` | diary ทุกชั่วโมงของ train รวมคน/วันที่ไม่เข้า candidate |
| `all_signal_hours.csv` | ค่าวัดจริงทุกชั่วโมงใน parquet ของผู้เข้าร่วมที่ตรวจ |
| `candidate_hours.csv` | จับคู่ candidate กับผลสแกน พร้อมสถานะ/เหตุผลคัดออก |
| `included_hours.csv` | ชั่วโมงที่ผ่านโครงสร้างและขั้นต่ำ duration เป็นรายชื่อสำหรับสกัด feature ต่อ ยังไม่ใช่ feature table |
| `excluded_hours.csv` | ทุก candidate hour ที่ถูกคัดออกพร้อมเหตุผล ไม่ลบหรือแก้ข้อมูลเดิม |
| `qc_reconciliation.csv` | ยอดนาทีจาก sample จริงเทียบ QC รายคน |
| `duration_sensitivity_train.csv` | จำนวนชั่วโมง/คนที่เหลือในแต่ละเกณฑ์ |
| `class_retention.csv` | จำนวนชั่วโมงแต่ละคะแนนก่อนและหลังใช้เกณฑ์ เพื่อดู selection bias ภายหลังเลือกเกณฑ์ |
| `plot_index.csv`, `plots/` | กราฟทั้งชั่วโมง, valid coverage, XYZ 20 วินาที และ PSD ของช่วง valid ต่อเนื่อง |
| `manifest.json` | snapshot, hash โค้ด/parquet/QC/diary และเกณฑ์ที่เลือก |

กราฟตัวอย่างเลือกแบบกำหนดซ้ำได้: ชั่วโมงที่ valid duration อยู่กึ่งกลางของแต่ละคะแนน, ชั่วโมงที่ valid น้อยที่สุด และชั่วโมงที่ใกล้เกณฑ์ที่สุด ไม่เลือกจากความเด่นของ tremor ในกราฟ ช่วงขยายใช้ 20 วินาทีแรกของ valid run ที่ยาวที่สุด ถ้าสั้นกว่านั้นแสดงเท่าที่มี ไม่ต่อช่วงข้าม gap และไม่อ่าน PSD ถ้าไม่มีช่วง valid อย่างน้อย 2 วินาที หากไม่มี valid เลย แสดง 20 วินาทีแรกของ excluded samples โดยติดป้ายแยกชัดเจน `--refresh-plots` วาดกราฟใหม่จาก cache/ผลตรวจเดิม ไม่เปลี่ยน cutoff และบันทึก hash โค้ดวาดกราฟแยกจากโค้ดสแกนเดิม

จำนวน flag minutes ซ้อนกันได้ ห้ามรวมเป็นเวลาที่ถูกตัดทั้งหมด กราฟแสดงค่าจาก clean parquet ซึ่งผ่าน calibration/resample/clip/noise harmonization แล้ว ไม่ใช่สัญญาณดิบก่อน preprocessing การไม่มี peak สั่นในช่วงขยายสั้น ๆ ไม่หักล้าง diary รายชั่วโมง และ peak เพียงอย่างเดียวไม่ยืนยันว่าเป็นอาการสั่นจาก PD

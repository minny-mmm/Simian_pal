# ใช้ข้อมูลที่มีทำโมเดลต่ออย่างไร

ผลตรวจ S3 วันที่ 19 ต.ค. 2026 หลังต่ออายุ SSO เปลี่ยนข้อสรุปด้านความพร้อมของไฟล์: มี QC 96 คน และ clean parquet จับคู่ครบทั้ง 96 คน ผ่าน QC 90 คน รวม 7,189.61 ชั่วโมงตาม QC ข้อมูลไม่ได้ขาดเท่าที่เห็นจากเครื่อง แต่ข้อจำกัด dataset/อุปกรณ์ซ้อนกับโรคยังอยู่

| กลุ่ม | train ที่ผ่าน QC | valid ที่ผ่าน QC | test ที่มี QC | ชั่วโมงตาม QC ของคนที่ผ่าน |
| --- | ---: | ---: | ---: | ---: |
| COPS PD | 31 | 1 | 0 | 2,302.91 |
| NHANES selected control | 58 | 0 | 0 | 4,886.70 |

QC บน S3 ของคนที่ผ่านตรงกับการตรวจ metadata/วัน/ชั่วโมงและ noise target ปัจจุบัน รวมถึง NHANES ที่หลักฐานในเครื่องเก่า ดังนั้นยังไม่มีเหตุให้สั่ง clean NHANES ใหม่ทั้งหมดจากผล local audit เดิม อย่างไรก็ตาม QC ไม่มี hash ของ config ทั้งชุดย้อนหลัง และยังไม่ได้สแกน parquet จึงไม่ใช่การรับรองความสดของผลทุกขั้น

## งานที่เหมาะจะเริ่มก่อน

ใช้ **COPS ทายการมีอาการสั่นรายชั่วโมง** จาก TremorScore 0 เทียบกับ 1/2 เป็น baseline แล้วค่อยเพิ่มงานระดับความรุนแรง 0/1/2 ซึ่งเป็นคะแนน diary ไม่ใช่ความรุนแรง PD ทั้งหมด ข้อดีคือมี target เปลี่ยนภายในกลุ่มที่ใช้อุปกรณ์ชนิดเดียวกัน จึงไม่ต้องทิ้ง COPS และลดปัญหาอุปกรณ์ตรงกับ target แม้ยังต้องควบคุมความต่างระหว่างคน กิจกรรม ยา และความคลาดเคลื่อนของ diary

การตรวจ diary เบื้องต้นก่อนสแกน parquet พบชั่วโมงผู้สมัครบนวันผ่าน QC ที่อยู่บ้าน ตื่น และระบุใส่อุปกรณ์ครบ ดังนี้ (ผลตรวจ valid samples จริงอยู่ใน README_signal_audit.md):

| split | คะแนน 0 | คะแนน 1 | คะแนน 2 |
| --- | ---: | ---: | ---: |
| train | 1,632 ชั่วโมง / 30 คน | 492 ชั่วโมง / 20 คน | 120 ชั่วโมง / 9 คน |
| valid | 56 ชั่วโมง / 1 คน | 6 ชั่วโมง / 1 คน | 0 |

คนหนึ่งอยู่ได้หลายคะแนน ห้ามบวกจำนวนคนข้ามคอลัมน์ คะแนน 2 กระจุกใน 9 คน และ validation ที่มีข้อมูลตอนนี้เพียงคนเดียว ยังไม่พอใช้สรุปผลทั่วไป แผนเริ่มต้นคือ grouped CV เฉพาะคนใน train เดิม เก็บ valid/test เดิมแยกไว้ และทำ clean ของคนใน split เหล่านั้นให้ครบก่อนยืนยันผล ห้ามเลือก fold หรือย้ายคนหลังเห็นคะแนน

ขั้นถัดไป:

เครื่องมือตรวจสัญญาณจริงสำหรับข้อ 1 อยู่ใน [README_signal_audit.md](README_signal_audit.md) ผลตรวจและเกณฑ์ที่เลือกจาก train บันทึกใน `reports/signal_audit/report.md` และ `manifest.json` ชั่วโมงที่ผ่านเป็น input สำหรับข้อ 2 ไม่ใช่ feature ที่สกัดเสร็จแล้ว

**ข้อ 1 ตรวจเสร็จแล้วสำหรับ train:** candidate 2,244 ชั่วโมงจับคู่โครงสร้างเวลาได้ครบ เลือกขั้นต่ำ 20 นาที valid จากกฎ coverage ที่บันทึกไว้ก่อน scan เหลือ 2,040 ชั่วโมงจาก 31 คน รวม valid duration 1,812.53 ชั่วโมง คัดออก 204 ชั่วโมงพร้อมเหตุผลใน `excluded_hours.csv` และตรวจกราฟตัวอย่างแล้ว พบ 132 candidate hours ที่ QC ไม่เหลือ valid เลย ควรเก็บตรวจความขัดแย้ง QC–diary ต่อ โดยยังไม่ปรับเกณฑ์ clean ขั้นถัดไปคือข้อ 2 สร้าง feature ใหม่

1. ตรวจ parquet ตาม candidate hour เพื่อวัดเวลาที่ valid จริงและจับคู่ Day/Time ของ diary กับสัญญาณ ทดลองตรวจกราฟจาก train ก่อน กำหนดขั้นต่ำ valid duration จาก train และบันทึกชั่วโมงที่ถูกคัดออก
2. สร้าง feature รายชั่วโมงใหม่โดยแบ่งตามเวลา/วัน ไม่แบ่งตาม row group และไม่ต่อช่วงข้ามช่องว่าง ตรวจคีย์ dataset/subject/day/hour ต้องไม่ซ้ำ เก็บ code/config/clean hashes
3. เริ่ม logistic regression แบบ regularized สำหรับมี/ไม่มีอาการสั่น และเทียบ dummy baseline รายงาน balanced accuracy, PR-AUC, recall ต่อคลาส และผลรายคน/ช่วงความเชื่อมั่นตามคน ใช้ imputation/scaling/class weighting จาก training fold เท่านั้น
4. แยก still_frac/rest_tremor_ratio ออกจาก baseline แล้วเพิ่มทีละตัวและทั้งคู่ ใช้แถวและ folds เดิมทุกชุด ห้ามแทน missing ด้วย 0 โดยไม่แยกความหมายว่าไม่มีอาการกับวัดไม่ได้
5. ตรวจความไวของผลต่อ QC โดยเฉพาะการตัดช่วงนิ่ง/ISM ซึ่งอาจเปลี่ยนส่วนของอาการที่เหลือให้เรียนรู้ เก็บช่วงถูกตัดไว้ตรวจ แต่ไม่ผ่อน QC หรือดึงช่วงนอน/ถอดอุปกรณ์เข้า training เพียงเพื่อเพิ่มจำนวนชั่วโมง

## ยังใช้ NHANES ได้

เก็บ NHANES เป็นกลุ่มอ้างอิงเชิงสำรวจ และอาจใช้ train-only data เรียน representation โดยไม่มี label ก่อน fine-tune งานอาการใน COPS แต่ต้องพิสูจน์ประโยชน์เทียบ baseline และห้ามใช้คน valid/test ฝึก representation หรือปรับ normalization แบบรวมทุกคน

หากเป้าหมายจำเป็นต้องเป็น PD vs control ยังทดลองได้ในชื่อ **COPS-vs-selected-NHANES cohort classifier** พร้อมข้อจำกัด ใช้ subject split, อายุ/เพศและจำนวนชั่วโมงที่สมดุล, train-only preprocessing, feature ablation และ diagnostic อุปกรณ์ แต่ noise matching, ComBat, domain-adversarial learning หรือ data augmentation ไม่สามารถพิสูจน์ว่าได้แยกโรคเมื่อ label ตรงกับ dataset/device ทั้งหมด การเพิ่ม window ไม่ได้เพิ่มจำนวนคนอิสระ

ทางเพิ่มหลักฐานโดยไม่เก็บข้อมูลใหม่ทั้งชุดคือทดสอบข้อมูลภายนอกที่มีทั้ง PD และ control บนอุปกรณ์เดียวกัน เช่น [PADS บน PhysioNet](https://physionet.org/content/parkinsons-disease-smartwatch/1.0.0/) ซึ่งมี 469 คนและใช้ Apple Watch Series 4 ทำงานเคลื่อนไหวที่กำหนด ต้องตรวจ feature ที่เทียบกันได้ก่อน เพราะเป็นงานสั้นในคลินิก ต่างจากข้อมูลใช้ชีวิตประจำวัน จึงไม่ควรรวมแถวกับ COPS/NHANES ทันที ผลผ่านใน PADS สนับสนุนได้บางส่วน ไม่รับรองการใช้งานชีวิตจริงโดยอัตโนมัติ

งานวิจัยเรื่อง [identity confounding ในข้อมูล PD ที่วัดซ้ำ](https://www.nature.com/articles/s41746-019-0178-x) สนับสนุนความจำเป็นของการแยกผู้เข้าร่วม ส่วน [การศึกษาการตรวจอาการด้วย wearable](https://www.nature.com/articles/s41746-018-0071-z) เป็นตัวอย่างของโจทย์อาการที่ใช้ประโยชน์จากข้อมูลผู้ป่วยโดยตรง

## รันตรวจซ้ำ

```powershell
.venv/Scripts/python.exe tools/audit_model_readiness.py
.venv/Scripts/python.exe -m pytest tests/test_audit_sources.py tests/test_model_readiness.py -q
```

เครื่องมืออ่าน inventory/QC/diary จาก S3 แล้วเขียน snapshot แยกใน `reports/model_readiness/` ไม่แก้ S3 หรือทับ QC เดิมใน `data/clean` รายงาน `coverage.csv` นับเฉพาะ roster ปัจจุบัน ไฟล์ `diary_labels_REVIEW_ONLY.csv` เป็น label สำหรับตรวจ ไม่มีการรับรองว่าแต่ละชั่วโมงมี valid samples พอหรือมี feature พร้อมเทรน และยังไม่ได้ฝึกโมเดลในรอบนี้

"""ข้อมูลที่จะโหลดเข้า DynamoDB (รัน: python load_dynamodb.py load)

โครงสร้างตามตาราง DynamoDB ใน website_structure.md (9 ตาราง) + pd_hourly_features
ผู้ป่วย 3 คน (is_demo = True) ค่ารายวัน / เพศ / อายุ / สถานะ มาจากข้อมูลวิจัยจริง ส่วนชื่อ / HN / หมอ เป็นของสมมติ:
- Patient-001 = COPS-12       (PD)
- Patient-002 = NHANES-73622  (ปกติ)
- Patient-003 = COPS-66       (PD) ค่าแย่กว่า baseline จนเกิดแจ้งเตือน (tremor เพิ่ม, ENMO ลด)
ทั้ง COPS และ NHANES ไม่มีวันที่จริง (มีแค่วันที่ 1, 2, 3 ...) จึงกำหนดวันแรกที่ใช้ = START_DATE ทุกคน

กติกา
- None = ไม่มีข้อมูล จะไม่ถูกเขียน (วันที่ขาด: ค่า = None แต่ใส่ _qc เสมอ)
- ทศนิยมใส่เป็น float ปกติ load_dynamodb.py แปลงเป็น Decimal ให้เอง
- วันที่ทุกที่เป็น ค.ศ. YYYY-MM-DD (หน้าเว็บแปลงเป็น พ.ศ. ตอนแสดง)
- ห้ามใส่ชื่อจริง / เลขบัตรประชาชนผู้ป่วยจริง (PDPA) ชื่อ/HN ในไฟล์นี้เป็นของสมมติ
"""
from datetime import date, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[3]
MODEL_VERSION = "demo-cohort-reference-v1"   # ค่ากลุ่มอ้างอิงใน pd_reference (ยังเป็นค่าจาก demo output.pdf)
METRICS_VERSION = "features-daily-v1"        # ค่ารายวันใน pd_daily_metrics (รวมจาก data/features ของ src.features.run)
START_DATE = date(2026, 10, 1)           # วันแรกที่ใช้ของทุกคน (ข้อมูลจริงไม่มีวันที่)
METRICS = ["enmo_g", "cadence_steps_s", "tremor_rest_fraction"]


def day_to_date(day):
    return (START_DATE + timedelta(days=day - 1)).isoformat()


# ================================================================ 1. pd_doctors
# key: doctor_id                                       ใช้ในหน้า: ทุกหน้า, 9 ตั้งค่า
DOCTORS = [
    {
        "doctor_id": "DR-001",
        "name": "พญ. ตัวอย่าง ใจดี",
        "position": "แพทย์ผู้ดูแล",
        "hospital": "โรงพยาบาลธรรมศาสตร์",
        "created_at": "2026-09-01",          # ก่อนเริ่มติดตามผู้ป่วย (START_DATE)
        "settings": {"theme": "light", "lang": "th", "date_format": "short",
                     "notify_alerts": True},
        "is_demo": True,
    },
]

# ================================================================ 3. pd_daily_metrics
# key: patient_id + date                               ใช้ในหน้า: 1, 2, 3, 4, 6
# date, enmo_g, cadence_steps_s, tremor_rest_fraction,
# wear_hours, no_signal_hours, low_quality_hours, enmo_qc, cadence_qc, tremor_qc
#
# ที่มา: data/features/<dataset>/<id>.csv (รายชั่วโมง 09:00-21:00) + data/clean/<dataset>/<id>.parquet
# - ใช้เฉพาะวันที่อยู่บ้านและบันทึกครบ 24 ชม. (ไม่รวมวันติด/ถอดเครื่อง และวันนอนโรงพยาบาลของ COPS)
# - enmo_g   = ค่าเฉลี่ยของ enmo_median รายชั่วโมง ถ่วงน้ำหนักด้วย valid_min
# - cadence  = ค่าเฉลี่ยของ step_freq_walk_median รายชั่วโมง ถ่วงน้ำหนักด้วย n_walk
# - tremor   = n_tremor รวมทั้งวัน / n_rest รวมทั้งวัน
# - ชั่วโมงในช่วง 09-21 (รวม = 12): wear = ใช้ได้, no_signal = ไม่มีข้อมูล/ไม่ได้ใส่ (nonwear),
#   low_quality = ใส่อยู่แต่ใช้ไม่ได้ (นิ่งผิดปกติ / ค่าผิดปกติ / clip / หลับ)
# - วันที่ใช้ได้ < 10 ชม. ทั้งวัน (เกณฑ์ clean) = insufficient_coverage ไม่มีค่า
DAILY_ROWS = {
    # COPS-12: วัน 3-6 ของต้นฉบับ
    "Patient-001": [
        ("2026-10-01", 0.02545, 1.7604, 0.05163, 8.18,  0.0, 3.82, "observed", "observed", "observed"),
        ("2026-10-02", 0.01325, 1.6842, 0.03039, 11.42, 0.0, 0.58, "observed", "observed", "observed"),
        ("2026-10-03", 0.02463, 1.7798, 0.02207, 9.03,  0.0, 2.97, "observed", "observed", "observed"),
        ("2026-10-04", 0.02221, 1.6933, 0.05102, 10.51, 0.0, 1.49, "observed", "observed", "observed"),
    ],
    # NHANES-73622: วัน 1-7 ของต้นฉบับ
    "Patient-002": [
        ("2026-10-01", 0.03906, 1.6277, 0.02637, 7.81, 0.0, 4.19, "observed", "observed", "observed"),
        ("2026-10-02", 0.07256, 1.6424, 0.06204, 9.27, 0.0, 2.73, "observed", "observed", "observed"),
        ("2026-10-03", 0.03439, 1.5749, 0.03178, 8.25, 0.0, 3.75, "observed", "observed", "observed"),
        ("2026-10-04", 0.04879, 1.5943, 0.04833, 9.42, 0.0, 2.58, "observed", "observed", "observed"),
        ("2026-10-05", 0.03456, 1.6609, 0.03464, 7.69, 0.0, 4.31, "observed", "observed", "observed"),
        ("2026-10-06", 0.04509, 1.6217, 0.02952, 8.69, 0.0, 3.31, "observed", "observed", "observed"),
        ("2026-10-07", 0.0332,  1.6871, 0.0198,  9.81, 0.0, 2.19, "observed", "observed", "observed"),
    ],
    # COPS-66: วัน 2-6 ของต้นฉบับ
    "Patient-003": [
        ("2026-10-01", 0.02247, 1.7711, 0.05299, 10.52, 0.0, 1.48, "observed", "observed", "observed"),
        ("2026-10-02", 0.01568, 1.7945, 0.03099, 11.04, 0.0, 0.96, "observed", "observed", "observed"),
        ("2026-10-03", 0.02398, 1.6287, 0.12305, 8.47,  0.0, 3.53, "observed", "observed", "observed"),
        ("2026-10-04", 0.01064, 1.7304, 0.13619, 9.47,  0.0, 2.53, "observed", "observed", "observed"),
        ("2026-10-05", 0.00898, 1.7196, 0.06477, 10.4,  0.0, 1.6,  "observed", "observed", "observed"),
    ],
}

DAILY_METRICS = [
    {"patient_id": pid, "date": dt, "day": (date.fromisoformat(dt) - START_DATE).days + 1,
     "enmo_g": enmo, "cadence_steps_s": cad, "tremor_rest_fraction": tremor,
     "wear_hours": wear, "no_signal_hours": nosig, "low_quality_hours": lowq,
     "enmo_qc": enmo_qc, "cadence_qc": cad_qc, "tremor_qc": tremor_qc,
     "model_version": METRICS_VERSION, "is_demo": True}
    for pid, rows in DAILY_ROWS.items()
    for dt, enmo, cad, tremor, wear, nosig, lowq, enmo_qc, cad_qc, tremor_qc in rows
]


def baseline(pid, n=3):
    """ค่าปกติของผู้ป่วยเอง = มัธยฐานของ n วันแรกที่มีข้อมูล แยกทีละตัวชี้วัด"""
    rows = sorted((r for r in DAILY_METRICS if r["patient_id"] == pid), key=lambda r: r["date"])
    out = {"days_used": n}
    for m in METRICS:
        vals = [r[m] for r in rows if r[m] is not None][:n]
        out[m] = median(vals) if vals else None
    return out


# ================================================================ 2. pd_patients
# key: patient_id · index by_doctor: doctor_id          ใช้ในหน้า: 2 รายชื่อ, 3 โปรไฟล์
# sex / status / watch_wrist จากข้อมูลจริง: COPS = Demographics.csv + cops_splits.csv (use_wrist),
#   NHANES = DEMO_H.xpt (RIAGENDR, RIDAGEYR) + label 0 = กลุ่มควบคุม
# birth_year ประมาณ = START_DATE.year - อายุ (ข้อมูลจริงมีแค่อายุ) หน้าเว็บจึงแสดงอายุตรงกับข้อมูล
# NHANES ใส่นาฬิกาข้างที่ไม่ถนัด (PAXHAND) แต่ไม่ระบุซ้าย/ขวา -> watch_wrist = None
PATIENTS = [
    {
        "patient_id": "Patient-001",       # COPS-12 อายุ 62
        "hn": "HN-000001",                 # สมมติ
        "display_name": "น.ส. ดีใจ ก.***",   # ชื่อสมมติ ปิดนามสกุล
        "doctor_id": "DR-001",
        "sex": "F",
        "birth_year": 1964,
        "status": "PD",                     # PD / สงสัย PD / ปกติ
        "follow_up": True,                  # ต้องติดตาม (แท็บ "ต้องติดตาม")
        "watch_wrist": "left",
        "monitoring_start": day_to_date(1),
        "is_demo": True,
        "baseline": baseline("Patient-001"),
    },
    {
        "patient_id": "Patient-002",       # NHANES-73622 อายุ 72
        "hn": "HN-000002",
        "display_name": "นาย ตัวอย่าง ข.***",
        "doctor_id": "DR-001",
        "sex": "M",
        "birth_year": 1954,
        "status": "ปกติ",
        "follow_up": False,
        "watch_wrist": None,
        "monitoring_start": day_to_date(1),
        "is_demo": True,
        "baseline": baseline("Patient-002"),
    },
    {
        "patient_id": "Patient-003",       # COPS-66 อายุ 52
        "hn": "HN-000003",
        "display_name": "นาย ทดสอบ ค.***",
        "doctor_id": "DR-001",
        "sex": "M",
        "birth_year": 1974,
        "status": "PD",
        "follow_up": True,
        "watch_wrist": "left",
        "monitoring_start": day_to_date(1),
        "is_demo": True,
        "baseline": baseline("Patient-003"),
    },
]

# ================================================================ 4. pd_reference
# key: metric + cohort                                 ใช้ในหน้า: 3, 6
# median จาก demo output.pdf (กลุ่มละ 6 คน); p25/p75 ยังไม่มีใน output -> None
# metric, cohort, p25, median, p75, n_people
REFERENCE_ROWS = [
    ("enmo_g",               "COPS",   None, 0.03525,   None, 6),
    ("enmo_g",               "NHANES", None, 0.06525,   None, 6),
    ("cadence_steps_s",      "COPS",   None, 1.43,      None, 6),
    ("cadence_steps_s",      "NHANES", None, 1.78,      None, 6),
    ("tremor_rest_fraction", "COPS",   None, 0.05,      None, 6),
    ("tremor_rest_fraction", "NHANES", None, 0.0166667, None, 6),
]

REFERENCE = [
    {"metric": m, "cohort": c, "p25": q25, "median": med, "p75": q75,
     "n_people": n, "version": MODEL_VERSION}
    for m, c, q25, med, q75, n in REFERENCE_ROWS
]

# ================================================================ 5. pd_alerts
# key: patient_id + created_at · index by_doctor: doctor_id + created_at   ใช้ในหน้า: 1, 7
# type: device (มาจากข้อมูลนาฬิกา) / assessment (มาจากผลประเมิน)
# กฎ metric: แย่กว่า baseline เกิน 20% ติดกัน 2 วันที่มีข้อมูล
WORSE_UP = {"tremor_rest_fraction": True, "cadence_steps_s": False, "enmo_g": False}
LABEL = {"tremor_rest_fraction": "Rest Tremor", "cadence_steps_s": "Walking Cadence",
         "enmo_g": "Activity Level"}


def metric_alerts(pid, doctor_id, run=2, threshold=0.2):
    base = baseline(pid)
    rows = sorted((r for r in DAILY_METRICS if r["patient_id"] == pid), key=lambda r: r["date"])
    out = []
    for m in METRICS:
        b, streak = base[m], []
        if not b:
            continue
        for r in rows:
            if r[m] is None:
                continue  # วันที่ขาดข้อมูลไม่นับ และไม่ตัด streak
            change = (r[m] - b) / b
            bad = change > threshold if WORSE_UP[m] else change < -threshold
            streak = streak + [r["date"]] if bad else []
            if len(streak) == run:
                word = "เพิ่มขึ้น" if change > 0 else "ลดลง"
                out.append({
                    "patient_id": pid, "doctor_id": doctor_id, "type": "device",
                    "created_at": f"{streak[-1]}T21:00:00", "metric": m,
                    "text": f"{LABEL[m]} {word} {abs(round(change * 100))}% เทียบค่าปกติของผู้ป่วย",
                    "rule": f"แย่กว่า baseline เกิน {int(threshold * 100)}% ติดกัน {run} วันที่มีข้อมูล",
                    "days": list(streak), "status": "new", "is_demo": True,
                })
    return out


# แจ้งเตือนแบบอื่น (เช่น ไม่ได้ใส่นาฬิกา) ต่อท้ายเป็น dict ได้ โดยใส่ key เดียวกับข้างบน
ALERTS = [a for p in PATIENTS for a in metric_alerts(p["patient_id"], p["doctor_id"])]

# ================================================================ 6. pd_notes
# key: patient_id + date                               ใช้ในหน้า: 3 (วางบนกราฟ)
# type: ปรับยา / นัดตรวจ / บันทึก
NOTES = [
    {"patient_id": "Patient-002", "date": day_to_date(6), "doctor_id": "DR-001",
     "type": "บันทึก", "text": "ผู้ป่วยแจ้งว่ามือสั่นมากขึ้นตอนเย็น", "is_demo": True},
    {"patient_id": "Patient-001", "date": day_to_date(1), "doctor_id": "DR-001",
     "type": "ปรับยา", "text": "เริ่ม Levodopa ขนาดเดิมต่อ", "is_demo": True},
    {"patient_id": "Patient-003", "date": day_to_date(2), "doctor_id": "DR-001",
          "type": "ปรับยา", "text": "เริ่ม Levodopa ขนาดเดิมต่อ", "is_demo": True},
]

# Patient-003 (COPS-66): ข้อมูลจริงจาก COPS-66_symptomdiary.csv (ผู้ป่วยบันทึกเองทุกชั่วโมง) วัน 2-6 ของต้นฉบับ
# ยา = ชื่อ ขนาด (mg) จำนวนครั้ง และเวลาที่กิน · อาการ = จำนวนชั่วโมงที่ผู้ป่วยติ๊กในวันนั้น
# NOTES += [
#     {"patient_id": "Patient-003", "date": dt, "doctor_id": "DR-001", "type": "บันทึก",
#      "text": f"diary ผู้ป่วย: {med} · {symptom}", "is_demo": True}
#     for dt, med, symptom in [
#         ("2026-10-01", "Levodopa/Carbidopa 100 mg x4 (08/11/14/18 น.), Amantadine 100 mg x3 (08/11/16 น.), "
#                        "Pramipexole ER 1.57 mg x1, Rasagiline 1 mg x1",
#          "สั่นเล็กน้อย 7 ชม. ไม่มีสั่นแรง / freezing / หกล้ม"),
#         ("2026-10-02", "Levodopa/Carbidopa 100 mg x3 (08/11/18 น.), Amantadine 100 mg x3 (08/11/16 น.), "
#                        "Pramipexole ER 1.57 mg x1, Rasagiline 1 mg x1",
#          "สั่นเล็กน้อย 6 ชม. ไม่มีสั่นแรง / freezing / หกล้ม"),
#         ("2026-10-03", "Levodopa/Carbidopa 100 mg x4 (07/12/15/18 น.), Amantadine 100 mg x3 (07/12/16 น.), "
#                        "Pramipexole ER 1.57 mg x1, Rasagiline 1 mg x1",
#          "สั่นเล็กน้อย 4 ชม. ไม่มีสั่นแรง / freezing / หกล้ม"),
#         ("2026-10-04", "Levodopa/Carbidopa 100 mg x4 (08/11/15/18 น.), Amantadine 100 mg x3 (08/13/16 น.), "
#                        "Pramipexole ER 1.57 mg x1, Rasagiline 1 mg x1",
#          "สั่นเล็กน้อย 9 ชม. (มากที่สุดในช่วงนี้) ไม่ได้บันทึก 4 ชม. ไม่มีสั่นแรง / freezing / หกล้ม"),
#         ("2026-10-05", "Levodopa/Carbidopa 100 mg x3 (07/11/16 น.), Amantadine 100 mg x3 (07/11/16 น.), "
#                        "Pramipexole ER 1.57 mg x1, Rasagiline 1 mg x1",
#          "สั่นเล็กน้อย 2 ชม. ไม่มีสั่นแรง / freezing / หกล้ม"),
#     ]
# ]

# ================================================================ 7. pd_devices
# key: patient_id                                      ใช้ในหน้า: 4 ข้อมูลจากอุปกรณ์
# last_sync = วันสุดท้ายที่มีข้อมูลใน pd_daily_metrics ของแต่ละคน
DEVICES = [
    {"patient_id": "Patient-001", "model": "Apple Watch Series 11", "serial": "apple-watch-0001",
     "firmware": "v1.0.0", "last_sync": f"{day_to_date(4)}T23:10:00", "is_demo": True},
    {"patient_id": "Patient-002", "model": "Apple Watch Series 11", "serial": "apple-watch-0002",
     "firmware": "v1.0.0", "last_sync": f"{day_to_date(7)}T22:45:00", "is_demo": True},
    {"patient_id": "Patient-003", "model": "Apple Watch Series 11", "serial": "apple-watch-0003",
     "firmware": "v1.0.0", "last_sync": f"{day_to_date(5)}T22:27:00", "is_demo": True},
]

# ================================================================ 8. pd_assessments
# key: patient_id + type_date (เช่น MDS-UPDRS#2026-09-15)   ใช้ในหน้า: 1, 5
# ทุกครั้งประเมินก่อนเริ่มใส่นาฬิกา (START_DATE = 2026-10-01)
# - Patient-001 / 002: คะแนนสมมติ; MDS-UPDRS Part III เต็ม 132 (33 ข้อ x 4) ใส่รายข้อเฉพาะที่ mockup แสดง
# - Patient-003: คะแนนจริงจาก COPS-66_UPDRS_OFF.csv เป็น UPDRS Part III แบบเดิม เต็ม 108 (27 ข้อ x 4)
#   วันที่ตีความจาก DaysSinceExamination = -1 (ตรวจหลังเริ่มบันทึก 1 วัน)
#   + MDS-UPDRS / MoCA ของ Patient-003 เป็นคะแนนสมมติ (note บอกไว้) ให้แท็บหลักในหน้า 5 มีข้อมูลเหมือนคนอื่น
#     รายข้อ MDS-UPDRS ใช้ค่าเดียวกับ UPDRS จริง, คะแนนรวมตั้งให้สูงกว่า 41 เล็กน้อยเพราะ MDS-UPDRS มีข้อมากกว่า
def assessment(pid, typ, day, total, max_score, items=None, assessor="DR-001", note=None, med_state=None):
    return {"patient_id": pid, "type_date": f"{typ}#{day}", "type": typ, "date": day,
            "total": total, "max": max_score, "items": items, "assessor_id": assessor,
            "note": note, "med_state": med_state, "is_demo": True}


ASSESSMENTS = [
    assessment("Patient-001", "MDS-UPDRS", "2026-07-15", 31, 132,
               {"rest_tremor": 2, "bradykinesia": 3, "rigidity": 2, "postural_stability": 1, "gait": 2}),
    assessment("Patient-001", "MDS-UPDRS", "2026-09-29", 28, 132,
               {"rest_tremor": 2, "bradykinesia": 3, "rigidity": 2, "postural_stability": 1, "gait": 2}),
    assessment("Patient-001", "MoCA", "2026-09-29", 25, 30),
    # Patient-002 เป็นคนปกติ (NHANES กลุ่มควบคุม) -> คะแนนสมมติอยู่ในช่วงปกติ
    # อายุ 72: ให้ 1 คะแนนที่การเคลื่อนไหวช้า / การเดิน (ช้าลงตามวัย ไม่ใช่ PD) ข้ออื่น 0 -> รวม 2
    assessment("Patient-002", "MDS-UPDRS", "2026-04-20", 2, 132,
               {"rest_tremor": 0, "bradykinesia": 1, "rigidity": 0, "postural_stability": 0, "gait": 1}),
    assessment("Patient-002", "CDR", "2026-04-20", 0, 3),
    assessment("Patient-003", "UPDRS-III-OFF", "2026-09-30", 41, 108,
               {"rest_tremor": 2, "bradykinesia": 2, "rigidity": 3, "postural_stability": 0, "gait": 1},
               med_state="OFF"),
    assessment("Patient-003", "MDS-UPDRS", "2026-06-30", 45, 132,
               {"rest_tremor": 2, "bradykinesia": 2, "rigidity": 2, "postural_stability": 0, "gait": 1},
               note="คะแนนสมมติ", med_state="OFF"),
    assessment("Patient-003", "MDS-UPDRS", "2026-09-30", 48, 132,
               {"rest_tremor": 2, "bradykinesia": 2, "rigidity": 3, "postural_stability": 0, "gait": 1},
               note="คะแนนสมมติ", med_state="OFF"),
    assessment("Patient-003", "MoCA", "2026-09-30", 26, 30, note="คะแนนสมมติ"),
]

# ================================================================ 9. pd_appointments
# key: doctor_id + slot (วันที่#เวลา#patient_id)          ใช้ในหน้า: 8 นัดหมาย
# status: มาแล้ว / รอพบ / ยังไม่มา
# นัดหลังช่วงใส่นาฬิกา (ข้อมูลสิ้นสุด 2026-10-07); สถานะตั้งตาม "วันนี้" = 2026-10-08
def appointment(day, time, pid, purpose, status):
    return {"doctor_id": "DR-001", "slot": f"{day}#{time}#{pid}", "date": day, "time": time,
            "patient_id": pid, "purpose": purpose, "status": status, "is_demo": True}


APPOINTMENTS = [
    appointment("2026-10-08", "09:00", "Patient-001", "ติดตามอาการ + ประเมิน MDS-UPDRS", "มาแล้ว"),
    appointment("2026-10-08", "10:00", "Patient-002", "ตรวจติดตามทั่วไป", "รอพบ"),
    appointment("2026-10-22", "13:00", "Patient-001", "ติดตามผลการปรับยา", "ยังไม่มา"),
    appointment("2026-10-22", "14:00", "Patient-003", "ติดตามอาการ", "ยังไม่มา"),
]

# ================================================================ 10. pd_hourly_features
# key: subject_id + hour_idx · อ่านตรงจาก output ของ python -m src.features.run (1 แถว = 1 ชั่วโมง)
# ไม่เปลี่ยนชื่อ column; ช่องว่าง (NaN) ไม่ถูกเขียน · ไฟล์ไหนไม่มีจะข้าม
FEATURE_DIR = ROOT / "data" / "features"
# subject_id ในตารางนี้คงเป็นรหัสต้นฉบับ (ไม่เปลี่ยนชื่อ/ค่า column)
FEATURE_SUBJECTS = [
    ("cops", "COPS-12"),             # = Patient-001
    ("nhanes", "NHANES-73622"),      # = Patient-002
    ("cops", "COPS-66"),             # = Patient-003
]


def hourly_features():
    import pandas as pd  # import ตอนใช้ เพื่อให้ตารางอื่นโหลดได้แม้ไม่มี pandas
    rows = []
    for ds, sid in FEATURE_SUBJECTS:
        path = FEATURE_DIR / ds / f"{sid}.csv"
        if not path.exists():
            print(f"  ไม่พบไฟล์ {path} ข้าม")
            continue
        df = pd.read_csv(path)
        df["subject_id"] = df["subject_id"].astype(str) if "subject_id" in df else sid
        rows += df.to_dict("records")
    return rows


TABLE_DATA = {
    "pd_doctors": DOCTORS,
    "pd_patients": PATIENTS,
    "pd_daily_metrics": DAILY_METRICS,
    "pd_reference": REFERENCE,
    "pd_alerts": ALERTS,
    "pd_notes": NOTES,
    "pd_devices": DEVICES,
    "pd_assessments": ASSESSMENTS,
    "pd_appointments": APPOINTMENTS,
    "pd_hourly_features": hourly_features,   # function: อ่านไฟล์ตอน load ตารางนี้เท่านั้น
}

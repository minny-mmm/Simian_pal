"""ข้อมูลที่จะโหลดเข้า DynamoDB (รัน: python load_dynamodb.py load)

โครงสร้างตามตาราง DynamoDB ใน website_structure.md (9 ตาราง) + pd_hourly_features
ผู้ป่วย 3 คน (is_demo = True) ค่ารายวัน / เพศ / อายุ / สถานะ มาจากข้อมูลวิจัยจริง ส่วนชื่อ / HN / หมอ เป็นของสมมติ:
- Patient-001 = COPS-7        (PD)    ชุด valid เดียวกับเดโม AI ของทีม (VALID-A/B)
- Patient-002 = NHANES-81262  (ปกติ)
- Patient-003 = NHANES-73558  (ปกติ) ค่าคงที่ตลอด 7 วัน ไม่เข้ากฎแจ้งเตือน
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
MODEL_VERSION = "real-data-pilot-aws-real-pilot-20261024-v1"   # ค่ากลุ่มอ้างอิงใน pd_reference = baseline ของเดโม AI ของทีม
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
        "name": "พญ. สิริสร ใจดี",
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
# date, source_day, enmo_g, cadence_steps_s, tremor_rest_fraction,
# wear_hours, no_signal_hours, low_quality_hours, enmo_qc, cadence_qc, tremor_qc
#
# ค่า enmo / cadence / tremor ตรงกับเดโม AI ของทีมทุกค่า (เกณฑ์เดียวกับ src/reference/daily.py ใน branch feat/lanna)
# ที่มา: data/features/<dataset>/<id>.csv (รายชั่วโมง) + data/clean/<dataset>/<id>.parquet
# - ใช้เฉพาะวันที่อยู่บ้านและบันทึกครบ 24 ชม. (ไม่รวมวันติด/ถอดเครื่อง และวันนอนโรงพยาบาลของ COPS)
# - source_day = เลขวันต้นฉบับ (เริ่ม 0) = ค่า day ของ API เดโม (หน้าเดโมแสดง day + 1)
# - ใช้ชั่วโมง 09-21 ที่มีข้อมูล >= 20 นาที; วันใช้ได้ต้องมี >= 6 ชม. และ >= 360 นาที
# - enmo_g   = ค่าเฉลี่ยของ enmo_median รายชั่วโมง ถ่วงน้ำหนักด้วย n_windows
# - cadence  = ค่าเฉลี่ยของ step_freq_walk_median รายชั่วโมง ถ่วงน้ำหนักด้วย n_walk (ต้องเดิน >= 2 ชม. และ >= 5 นาที)
# - tremor   = n_tremor รวมทั้งวัน / n_rest รวมทั้งวัน (ต้องมีช่วงพัก >= 2 ชม. และ >= 5 นาที)
# - ไม่ผ่านเกณฑ์ = None + insufficient_coverage (เดโมเรียก insufficient_data)
# - ชั่วโมงในช่วง 09-21 (รวม = 12): wear = ใช้ได้, no_signal = ไม่มีข้อมูล/ไม่ได้ใส่ (nonwear),
#   low_quality = ใส่อยู่แต่ใช้ไม่ได้ (นิ่งผิดปกติ / ค่าผิดปกติ / clip / หลับ)
# คำนวณใหม่ได้ด้วย (พิมพ์ทั้ง DAILY_ROWS และ DAILY_WINDOWS): python web/backend/database/make_daily_rows.py cops/COPS-7 nhanes/NHANES-81262 nhanes/NHANES-73558
# เทียบกับเดโม: เพิ่ม --check <daily.csv ใน valid bundle ของเดโม> หน้ารายชื่อ
DAILY_ROWS = {
    # COPS-7 (VALID-A): วัน 3-6 ของต้นฉบับ · เดินน้อยกว่าเกณฑ์ทุกวัน -> ไม่มี cadence
    "Patient-001": [
        ("2026-10-01", 3, 0.01384, None, 0.12621, 11.28, 0.0, 0.72, "observed", "insufficient_coverage", "observed"),
        ("2026-10-02", 4, 0.01598, None, 0.09618, 10.87, 0.0, 1.13, "observed", "insufficient_coverage", "observed"),
        ("2026-10-03", 5, 0.0132,  None, 0.05886, 10.39, 0.0, 1.61, "observed", "insufficient_coverage", "observed"),
        ("2026-10-04", 6, 0.01379, None, 0.16231, 10.97, 0.0, 1.03, "observed", "insufficient_coverage", "observed"),
    ],
    # NHANES-81262 (VALID-B): วัน 1-7 ของต้นฉบับ
    "Patient-002": [
        ("2026-10-01", 1, 0.02022, None,   0.0233,  11.8,  0.0, 0.2,  "observed", "insufficient_coverage", "observed"),
        ("2026-10-02", 2, 0.02014, 1.5693, 0.025,   11.35, 0.0, 0.65, "observed", "observed", "observed"),
        ("2026-10-03", 3, 0.01952, 1.7638, 0.03267, 11.54, 0.0, 0.46, "observed", "observed", "observed"),
        ("2026-10-04", 4, 0.02099, 1.5255, 0.03243, 11.3,  0.0, 0.7,  "observed", "observed", "observed"),
        ("2026-10-05", 5, 0.01985, None,   0.05357, 11.43, 0.0, 0.57, "observed", "insufficient_coverage", "observed"),
        ("2026-10-06", 6, 0.01769, None,   0.08482, 11.02, 0.0, 0.98, "observed", "insufficient_coverage", "observed"),
        ("2026-10-07", 7, 0.01941, 1.5652, 0.04345, 11.34, 0.0, 0.66, "observed", "observed", "observed"),
    ],
    # NHANES-73558 (ไม่มีในเดโม; เป็น TRAIN-09 ที่ใช้ fit ค่าอ้างอิง): วัน 1-7 ของต้นฉบับ
    "Patient-003": [
        ("2026-10-01", 1, 0.02368, 1.7547, 0.02342, 11.35, 0.0, 0.65, "observed", "observed", "observed"),
        ("2026-10-02", 2, 0.01454, 1.6767, 0.02515, 10.81, 0.0, 1.19, "observed", "observed", "observed"),
        ("2026-10-03", 3, 0.01824, 1.7636, 0.0235,  10.83, 0.0, 1.17, "observed", "observed", "observed"),
        ("2026-10-04", 4, 0.02207, 1.866,  0.04196, 11.36, 0.0, 0.64, "observed", "observed", "observed"),
        ("2026-10-05", 5, 0.02466, 1.6715, 0.0261,  10.76, 0.0, 1.24, "observed", "observed", "observed"),
        ("2026-10-06", 6, 0.02014, None,   0.02102, 8.99,  0.0, 3.01, "observed", "insufficient_coverage", "observed"),
        ("2026-10-07", 7, 0.02198, 1.7767, 0.02566, 10.96, 0.0, 1.04, "observed", "observed", "observed"),
    ],
}

# จำนวน window ที่ใช้คิดค่าแต่ละตัว (= metric_windows ของเดโม) + เหตุผล QC (= reasons ของเดโม) ทุกค่าตรงกับเดโม
# ใช้คิดค่าเฉลี่ยหลายวันแบบถ่วงน้ำหนัก (app/metrics.py): tremor = sum(n_tremor)/sum(n_rest),
#   cadence ถ่วงด้วย n_walk, ENMO ถ่วงด้วย n_windows · หน้าคุณภาพข้อมูลใช้บอกเหตุผลที่วันนั้นขาดค่าได้
# date, n_windows, n_walk, n_rest, n_tremor, day_valid_minutes, day_eligible_hours, qc_reasons
COV = {"cadence": "metric_coverage_below_minimum"}   # เดินน้อยกว่าเกณฑ์ (2 ชม. / 5 นาที)
DAILY_WINDOWS = {
    "Patient-001": [
        ("2026-10-01", 4043, 2,  1862, 235, 673.83, 12, COV),
        ("2026-10-02", 3899, 4,  1518, 146, 649.83, 12, COV),
        ("2026-10-03", 3727, 12, 1495, 88,  621.17, 12, COV),
        ("2026-10-04", 3927, 2,  1836, 298, 654.5,  12, COV),
    ],
    "Patient-002": [
        ("2026-10-01", 4202, 29,  1073, 25,  700.33, 12, COV),
        ("2026-10-02", 4003, 46,  1200, 30,  667.17, 12, {}),
        ("2026-10-03", 4107, 106, 1316, 43,  684.5,  12, {}),
        ("2026-10-04", 3998, 68,  1295, 42,  666.33, 12, {}),
        ("2026-10-05", 4045, 25,  1232, 66,  674.17, 12, COV),
        ("2026-10-06", 3873, 27,  1568, 133, 645.5,  12, COV),
        ("2026-10-07", 4002, 56,  1358, 59,  667.0,  12, {}),
    ],
    "Patient-003": [
        ("2026-10-01", 3966, 80, 1238, 29, 661.0,  12, {}),
        ("2026-10-02", 3752, 47, 1630, 41, 625.33, 12, {}),
        ("2026-10-03", 3787, 89, 1404, 33, 631.17, 12, {}),
        ("2026-10-04", 3978, 79, 1287, 54, 663.0,  12, {}),
        ("2026-10-05", 3727, 48, 1226, 32, 621.17, 12, {}),
        ("2026-10-06", 3022, 26, 1332, 28, 503.67, 12, COV),
        ("2026-10-07", 3828, 60, 1286, 33, 638.0,  12, {}),
    ],
}
WINDOWS = {(pid, r[0]): dict(zip(("n_windows", "n_walk", "n_rest", "n_tremor", "day_valid_minutes",
                                  "day_eligible_hours", "qc_reasons"), r[1:]))
           for pid, rows in DAILY_WINDOWS.items() for r in rows}

DAILY_METRICS = [
    {"patient_id": pid, "date": dt, "day": (date.fromisoformat(dt) - START_DATE).days + 1, "source_day": src,
     "enmo_g": enmo, "cadence_steps_s": cad, "tremor_rest_fraction": tremor,
     "wear_hours": wear, "no_signal_hours": nosig, "low_quality_hours": lowq,
     "enmo_qc": enmo_qc, "cadence_qc": cad_qc, "tremor_qc": tremor_qc,
     **WINDOWS[(pid, dt)], "model_version": METRICS_VERSION, "is_demo": True}
    for pid, rows in DAILY_ROWS.items()
    for dt, src, enmo, cad, tremor, wear, nosig, lowq, enmo_qc, cad_qc, tremor_qc in rows
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
# ai_subject = ชื่อแฝงใน API เดโม AI ของทีม (VALID-A = COPS-7, VALID-B = NHANES-81262) None = ไม่มีผล AI
PATIENTS = [
    {
        "patient_id": "Patient-001",       # COPS-7 อายุ 65 หญิง (H&Y 3, tremor-dominant, DBS)
        "hn": "HN-000001",                 # สมมติ
        "display_name": "น.ส. ดีใจ ก.***",   # ชื่อสมมติ ปิดนามสกุล
        "doctor_id": "DR-001",
        "sex": "F",
        "birth_year": 1961,
        "status": "PD",                     # PD / สงสัย PD / ปกติ
        "follow_up": True,                  # ต้องติดตาม (แท็บ "ต้องติดตาม")
        "watch_wrist": "left",
        "ai_subject": "VALID-A",
        "monitoring_start": day_to_date(1),
        "is_demo": True,
        "baseline": baseline("Patient-001"),
    },
    {
        "patient_id": "Patient-002",       # NHANES-81262 อายุ 71 หญิง
        "hn": "HN-000002",
        "display_name": "นาง พัณณิชา ข.***",
        "doctor_id": "DR-001",
        "sex": "F",
        "birth_year": 1955,
        "status": "ปกติ",
        "follow_up": True,                  # มี alert tremor เพิ่ม + ผู้ป่วยแจ้งมือสั่นตอนเย็น -> หมอติดตามต่อ
        "watch_wrist": None,
        "ai_subject": "VALID-B",
        "monitoring_start": day_to_date(1),
        "is_demo": True,
        "baseline": baseline("Patient-002"),
    },
    {
        "patient_id": "Patient-003",       # NHANES-73558 อายุ 54 ชาย
        "hn": "HN-000003",
        "display_name": "นาย สรวิทธิ์ ค.***",
        "doctor_id": "DR-001",
        "sex": "M",
        "birth_year": 1972,
        "status": "ปกติ",
        "follow_up": False,
        "watch_wrist": None,
        "ai_subject": None,
        "monitoring_start": day_to_date(1),
        "is_demo": True,
        "baseline": baseline("Patient-003"),
    },
]

# ================================================================ 4. pd_reference
# key: metric + cohort                                 ใช้ใน: GET /api/reports/percentiles
# ค่าจาก baseline ที่เดโม AI ของทีมใช้ (baseline.json ใน S3: data/models/reference-v1/aws-real-pilot-20261024-v1/
#   local_py312_compat, artifact 9b65d11f…) = ค่าเดียวกับ reference_comparisons ในหน้ารายงาน
# fit จากชุด train (COPS 8 คน / NHANES 8 คน) ถ่วงน้ำหนักให้แต่ละคนเท่ากัน · quantile ไม่ interpolate
# cadence มีคนที่ข้อมูลเดินพอ COPS 7 / NHANES 6 คน
# metric, cohort, p25, median, p75, n_people, n_days
REFERENCE_ROWS = [
    ("enmo_g",               "COPS",   0.0159925901418679, 0.0211329671121317, 0.0263659602496753, 8, 40),
    ("enmo_g",               "NHANES", 0.0220709040535444, 0.0260830407872579, 0.0328065148390804, 8, 51),
    ("cadence_steps_s",      "COPS",   1.709536315794579,  1.8165591500234075, 1.894191385783487,  7, 29),
    ("cadence_steps_s",      "NHANES", 1.7539085653876945, 1.8335235009628708, 1.9426475585428704, 6, 36),
    ("tremor_rest_fraction", "COPS",   0.0226854690374003, 0.0487084870848708, 0.0886392009987515, 8, 40),
    ("tremor_rest_fraction", "NHANES", 0.0271646859083191, 0.0401554404145077, 0.057037037037037,  8, 51),
]

REFERENCE = [
    {"metric": m, "cohort": c, "p25": q25, "median": med, "p75": q75,
     "n_people": n, "n_days": nd, "version": MODEL_VERSION}
    for m, c, q25, med, q75, n, nd in REFERENCE_ROWS
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
     "type": "ปรับยา", "text": "เพิ่ม Levodopa มื้อเย็นอีก 1 มื้อ เพราะยังสั่นขณะพัก (DBS ตั้งค่าเดิม)", "is_demo": True},
    {"patient_id": "Patient-003", "date": day_to_date(1), "doctor_id": "DR-001",
     "type": "บันทึก", "text": "ตรวจร่างกายปกติ ไม่มีอาการทางระบบประสาท ใส่นาฬิกาเก็บค่าอ้างอิง 7 วัน", "is_demo": True},
]

# ================================================================ 7. pd_devices
# key: patient_id                                      ใช้ในหน้า: 4 ข้อมูลจากอุปกรณ์
# last_sync = วันสุดท้ายที่มีข้อมูลใน pd_daily_metrics ของแต่ละคน
DEVICES = [
    {"patient_id": "Patient-001", "model": "Apple Watch Series 11", "serial": "apple-watch-0001",
     "firmware": "v1.0.0", "last_sync": f"{day_to_date(4)}T23:10:00", "is_demo": True},
    {"patient_id": "Patient-002", "model": "Apple Watch Series 11", "serial": "apple-watch-0002",
     "firmware": "v1.0.0", "last_sync": f"{day_to_date(7)}T22:45:00", "is_demo": True},
    {"patient_id": "Patient-003", "model": "Apple Watch Series 11", "serial": "apple-watch-0003",
     "firmware": "v1.0.0", "last_sync": f"{day_to_date(7)}T22:27:00", "is_demo": True},
]

# ================================================================ 8. pd_assessments
# key: patient_id + type_date (เช่น MDS-UPDRS#2026-09-15)   ใช้ในหน้า: 1, 5
# ทุกครั้งประเมินก่อนเริ่มใส่นาฬิกา (START_DATE = 2026-10-01) คะแนนสมมติทั้งหมด
# "MDS-UPDRS" = Part III (motor) อย่างเดียว เต็ม 132 (33 ข้อ x 4) คะแนนสูง = อาการมาก
# ใส่รายข้อเฉพาะที่ mockup แสดง (ข้ออื่นรวมอยู่ใน total) · MoCA เต็ม 30 คะแนนสูง = ดี (ปกติ >= 26)
def assessment(pid, typ, day, total, max_score, items=None, assessor="DR-001", note=None, med_state=None):
    return {"patient_id": pid, "type_date": f"{typ}#{day}", "type": typ, "date": day,
            "total": total, "max": max_score, "items": items, "assessor_id": assessor,
            "note": note, "med_state": med_state, "is_demo": True}


ASSESSMENTS = [
    # Patient-001: H&Y 3, tremor-dominant, มี DBS -> สั่นขณะพักเด่นกว่าข้ออื่น ประเมินตอน ON (กินยา + เปิด DBS)
    # กรกฎาคม -> กันยายน ดีขึ้นเล็กน้อยหลังปรับตั้ง DBS (rigidity ลด) แต่ยังสั่น จึงปรับยาเพิ่มวันที่ 1 ต.ค. (pd_notes)
    # MoCA 25 = ต่ำกว่าเกณฑ์เล็กน้อย (MCI พบบ่อยใน PD ระยะนี้)
    assessment("Patient-001", "MDS-UPDRS", "2026-07-15", 31, 132,
               {"rest_tremor": 3, "bradykinesia": 2, "rigidity": 2, "postural_stability": 1, "gait": 1},
               med_state="ON"),
    assessment("Patient-001", "MDS-UPDRS", "2026-09-29", 28, 132,
               {"rest_tremor": 3, "bradykinesia": 2, "rigidity": 1, "postural_stability": 1, "gait": 1},
               med_state="ON"),
    assessment("Patient-001", "MoCA", "2026-09-29", 25, 30),
    # Patient-002: คนปกติ อายุ 71 ให้ 1 คะแนนที่การเคลื่อนไหวช้า / การเดิน (ช้าลงตามวัย ไม่ใช่ PD) ข้ออื่น 0 -> รวม 2
    assessment("Patient-002", "MDS-UPDRS", "2026-04-20", 2, 132,
               {"rest_tremor": 0, "bradykinesia": 1, "rigidity": 0, "postural_stability": 0, "gait": 1}),
    assessment("Patient-002", "MoCA", "2026-04-20", 27, 30),
    # Patient-003: คนปกติ อายุ 54 ตรวจไม่พบอาการ -> 0 ทุกข้อ
    assessment("Patient-003", "MDS-UPDRS", "2026-09-25", 0, 132,
               {"rest_tremor": 0, "bradykinesia": 0, "rigidity": 0, "postural_stability": 0, "gait": 0}),
    assessment("Patient-003", "MoCA", "2026-09-25", 28, 30),
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
    appointment("2026-10-22", "14:00", "Patient-003", "แจ้งผลการใส่นาฬิกา 7 วัน", "ยังไม่มา"),
]

# ================================================================ 10. pd_hourly_features
# key: subject_id + hour_idx · อ่านตรงจาก output ของ python -m src.features.run (1 แถว = 1 ชั่วโมง)
# ไม่เปลี่ยนชื่อ column; ช่องว่าง (NaN) ไม่ถูกเขียน · ไฟล์ไหนไม่มีจะข้าม
FEATURE_DIR = ROOT / "data" / "features"
# subject_id ในตารางนี้คงเป็นรหัสต้นฉบับ (ไม่เปลี่ยนชื่อ/ค่า column)
FEATURE_SUBJECTS = [
    ("cops", "COPS-7"),              # = Patient-001
    ("nhanes", "NHANES-81262"),      # = Patient-002
    ("nhanes", "NHANES-73558"),      # = Patient-003
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

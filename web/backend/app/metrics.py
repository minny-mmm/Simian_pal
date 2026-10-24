"""คำนวณค่าที่หน้าเว็บใช้จากค่ารายวัน (ไม่เก็บลง DB)"""
from datetime import date, timedelta
from statistics import mean

# ตัวชี้วัดที่โมเดลส่งมา -> หน่วยที่แสดงบนเว็บ
METRICS = {
    "tremor": {"field": "tremor_rest_fraction", "scale": 100, "unit": "%", "label": "Rest Tremor"},
    "cadence": {"field": "cadence_steps_s", "scale": 60, "unit": "ก้าว/นาที", "label": "Walking Cadence"},
    "activity": {"field": "enmo_g", "scale": 1000, "unit": "mg (ENMO)", "label": "Activity Level"},  # g ค่าเล็กมาก ปัด 2 ตำแหน่งแล้วเหลือ 0.02 ทุกคน
}


def window(rows, days, end=None):
    """แถวของ N วันล่าสุด นับจากวันล่าสุดที่มีข้อมูล (หรือ end)"""
    if not rows:
        return []
    end = end or max(r["date"] for r in rows)
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    return [r for r in rows if start <= r["date"] <= end]


def values(rows, key):
    m = METRICS[key]
    return [r[m["field"]] * m["scale"] for r in rows if r.get(m["field"]) is not None]


def pct_change(new, old):
    if new is None or old in (None, 0):
        return None
    return round((new - old) / old * 100, 1)


def summary(rows, days=7, end=None):
    """การ์ดสรุป: ค่าเฉลี่ย N วันล่าสุด (หรือ N วันที่จบที่ end) เทียบกับ N วันก่อนหน้า"""
    cur = window(rows, days, end)
    if not cur:
        return {}
    prev_end = (date.fromisoformat(min(r["date"] for r in cur)) - timedelta(days=1)).isoformat()
    prev = window(rows, days, prev_end)
    out = {}
    for key, m in METRICS.items():
        c, p = values(cur, key), values(prev, key)
        now = round(mean(c), 2) if c else None
        out[key] = {"label": m["label"], "unit": m["unit"], "value": now,
                    "change_pct": pct_change(now, mean(p) if p else None),
                    "valid_days": len(c)}
    return out


def series(rows, key, days):
    """จุดบนกราฟรายวัน + ค่าเฉลี่ย/สูงสุด/ต่ำสุด"""
    m = METRICS[key]
    pts = [{"date": r["date"],
            "value": None if r.get(m["field"]) is None else round(r[m["field"]] * m["scale"], 3),
            "qc": r.get(m["field"].split("_")[0] + "_qc")}
           for r in sorted(window(rows, days), key=lambda r: r["date"])]
    vals = [p["value"] for p in pts if p["value"] is not None]
    stats = {"mean": round(mean(vals), 3), "max": max(vals), "min": min(vals)} if vals else {}
    return {"metric": key, "label": m["label"], "unit": m["unit"], "points": pts, **stats}
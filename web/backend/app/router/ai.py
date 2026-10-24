"""ผลวิเคราะห์ + คำอธิบายจากเดโม AI ของทีม (เรียกผ่าน app/reference.py ซึ่งแนบรหัสฝั่ง server)

เฉพาะผู้ป่วยที่มี ai_subject (Patient-001 = VALID-A, Patient-002 = VALID-B)
day ของเดโม = source_day ใน pd_daily_metrics -> แปลงเป็นวันที่ของเรา (หน้าเดโมแสดง day + 1)
ตัวเลขทั้งหมดมาจาก analysis ของเดโมตรงๆ ไม่คำนวณใหม่ · null = คำนวณไม่ได้ ไม่ใช่ 0
ระยะห่างจากกลุ่มอ้างอิงไม่ใช่คะแนนความรุนแรงหรือโอกาสเป็น PD
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app import reference
from app.auth import current_doctor
from app.router.name_patient import daily_rows, get_own_patient

router = APIRouter(prefix="/api/patients", tags=["ai"])

# ชื่อ metric ของเดโม -> ชื่อสั้นที่หน้าเว็บใช้ (เหมือน /metrics?metric=)
KEY = {"enmo_hourly_median_mean_g": "enmo",
       "walking_cadence_hourly_median_mean_hz": "cadence",
       "rest_tremor_window_fraction": "tremor"}
NO_AI = "ผู้ป่วยนี้ยังไม่มีผลวิเคราะห์ AI"


def ai_patient(patient_id, doctor_id, response):
    """ผู้ป่วยของหมอคนนี้ที่มี ai_subject + ตาราง day ของเดโม -> วันที่"""
    response.headers["Cache-Control"] = "no-store"
    p = get_own_patient(patient_id, doctor_id)
    if not p.get("ai_subject"):
        raise HTTPException(status_code=404, detail=NO_AI)
    dates = {int(r["source_day"]): r["date"] for r in daily_rows(patient_id) if r.get("source_day") is not None}
    return p["ai_subject"], dates


def compare(c):
    return {k: c.get(k) for k in ("status", "reason", "reference_median", "signed_distance", "standardized_deviation")}


def row(r, dates):
    prev = r.get("previous_day") or {}
    refs = r.get("reference_comparisons") or {}
    return {
        "date": dates.get(r["day"]), "day": r["day"], "display_day": r["day"] + 1,
        "metric": r["metric"], "key": KEY.get(r["metric"]), "unit": r["unit"],
        "value": r.get("value"), "status": r.get("status"),
        "qc_reasons": (r.get("qc") or {}).get("reasons", []),
        "cops": compare(refs.get("cops") or {}), "nhanes": compare(refs.get("nhanes") or {}),
        "previous_day": {k: prev.get(k) for k in ("day", "delta", "status", "reason")},
    }


def meta(doc):
    return {k: doc.get(k) for k in ("schema_version", "baseline_version", "data_origin")}


@router.get("/{patient_id}/ai/analysis")
def ai_analysis(patient_id: str, response: Response, doctor_id: str = Depends(current_doctor)):
    subject, dates = ai_patient(patient_id, doctor_id, response)
    doc = reference.get("analysis", subject=subject)
    rows = [row(r, dates) for r in doc.get("analysis", []) if r.get("subject_id") == subject]
    days = sorted({(r["day"], r["date"]) for r in rows})
    return {"patient_id": patient_id, **meta(doc),
            "days": [{"day": d, "date": dt, "display_day": d + 1} for d, dt in days],
            "rows": sorted(rows, key=lambda r: (r["metric"], r["day"]))}


@router.get("/{patient_id}/ai/explanation")
def ai_explanation(patient_id: str, response: Response,
                   date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                   doctor_id: str = Depends(current_doctor)):
    """คำอธิบายของวันเดียว (ไม่ระบุ date = วันล่าสุด) — เรียกเมื่อหมอกดปุ่มเท่านั้น"""
    subject, dates = ai_patient(patient_id, doctor_id, response)
    if not dates:
        raise HTTPException(status_code=404, detail=NO_AI)
    by_date = {dt: d for d, dt in dates.items()}
    date = date or max(by_date)
    if date not in by_date:
        raise HTTPException(status_code=404, detail="ไม่มีผลวิเคราะห์ AI ของวันนี้")
    doc = reference.get("explanation", subject=subject, day=by_date[date])
    exp = doc.get("explanation") or {}
    llm = exp.get("generation_kind") == "llm"
    return {
        "patient_id": patient_id, "date": date, "day": by_date[date], "display_day": by_date[date] + 1,
        **meta(exp),
        "generation_kind": exp.get("generation_kind"),
        "label": "LLM" if llm else "Template fallback",
        "model_version": exp.get("model_version"),
        "fallback_reason": None if llm else (doc.get("validation") or {}).get("reason"),
        "explanations": [{"metric": e["metric"], "key": KEY.get(e["metric"]), "unit": e.get("unit"),
                          "text": e.get("text"), "evidence": e.get("evidence")}
                         for e in exp.get("explanations", [])],
        # ตัวเลขของวันนั้นจาก analysis (หน้าเว็บแสดงตัวเลขจากตรงนี้ ไม่ดึงจากข้อความ LLM)
        "rows": [row(r, dates) for r in (doc.get("analysis") or {}).get("analysis", [])],
    }

"""ผลวิเคราะห์ + คำอธิบายจากเดโม AI ของทีม (เรียกผ่าน app/reference.py ซึ่งแนบรหัสฝั่ง server)

เฉพาะผู้ป่วยที่มี ai_subject (Patient-001 = VALID-A, Patient-002 = VALID-B)
day ของเดโม = source_day ใน pd_daily_metrics -> แปลงเป็นวันที่ของเรา (หน้าเดโมแสดง day + 1)
ตัวเลขทั้งหมดมาจาก analysis ของเดโมตรงๆ (ส่งต่อทุก field ตาม schema 1.0.0) ไม่คำนวณใหม่ · null = คำนวณไม่ได้ ไม่ใช่ 0
ระยะห่างจากกลุ่มอ้างอิงไม่ใช่คะแนนความรุนแรงหรือโอกาสเป็น PD

เก็บผลไว้ใน pd_ai_results (อ่านก่อน -> ไม่มีค่อยเรียกเดโมแล้วบันทึก) จึงดูผลเดิมได้แม้โควตาเดโมเต็ม/เดโมหมดอายุ
- analysis เก็บ 1 แถว/ผู้ป่วย · คำอธิบายเก็บ 1 แถว/วัน เฉพาะที่มาจาก LLM (template ไม่เก็บ ขอใหม่ได้)
- ข้อมูลตั้งต้นดึงจาก S3 ของเดโมด้วย database/import_ai_results.py
"""
import json
from datetime import datetime, timezone
from typing import Optional

from boto3.dynamodb.conditions import Key

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app import reference
from app.auth import current_doctor
from app.db import query_all, table
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


def row(r, dates):
    """แถวของเดโมตามเดิมทุก field (schema 1.0.0 หน้ารายงานใช้ quantiles / n_participants / qc ด้วย)
    + วันที่ของเรา, เลขวันแบบหน้าเดโม (day + 1) และชื่อสั้นของ metric"""
    return {**r, "date": dates.get(r["day"]), "display_day": r["day"] + 1, "key": KEY.get(r["metric"])}


def meta(doc):
    return {k: doc.get(k) for k in ("schema_version", "baseline_version", "data_origin")}


def stored(patient_id, result_key):
    """ผลที่เก็บไว้ (doc = JSON ตามที่เดโมตอบ) ไม่มี = None"""
    item = table("ai_results").get_item(Key={"patient_id": patient_id, "result_key": result_key}).get("Item")
    return item and {"doc": json.loads(item["doc"]), "saved_at": item.get("saved_at")}


def save(patient_id, result_key, doc, **extra):
    """บันทึกผลจากเดโม บันทึกไม่ได้ก็ยังตอบหน้าเว็บตามปกติ (ครั้งหน้าค่อยเรียกเดโมใหม่)"""
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        table("ai_results").put_item(Item={"patient_id": patient_id, "result_key": result_key, "source": "demo-api",
                                           "saved_at": saved_at, "doc": json.dumps(doc, ensure_ascii=False), **extra})
    except Exception:
        pass
    return saved_at


def fetch_or_stored(patient_id, result_key, path, keep=lambda doc: True, **query):
    """ผลที่เก็บไว้ก่อน ไม่มีค่อยเรียกเดโม -> (doc, ที่มา, เวลาที่บันทึก)"""
    hit = stored(patient_id, result_key)
    if hit:
        return hit["doc"], "saved", hit["saved_at"]
    doc = reference.get(path, **query)
    saved_at = save(patient_id, result_key, doc) if keep(doc) else None
    return doc, "demo", saved_at


def explanation_out(patient_id, date, day, doc, dates, source, saved_at):
    exp = doc.get("explanation") or {}
    llm = exp.get("generation_kind") == "llm"
    return {
        "patient_id": patient_id, "date": date, "day": day, "display_day": day + 1,
        **meta(exp),
        "generation_kind": exp.get("generation_kind"),
        "label": "LLM" if llm else "Template fallback",
        "model_version": exp.get("model_version"),
        "fallback_reason": None if llm else (doc.get("validation") or {}).get("reason"),
        "source": source, "saved_at": saved_at,
        "explanations": [{"metric": e["metric"], "key": KEY.get(e["metric"]), "unit": e.get("unit"),
                          "text": e.get("text"), "evidence": e.get("evidence")}
                         for e in exp.get("explanations", [])],
        # ตัวเลขของวันนั้นจาก analysis (หน้าเว็บแสดงตัวเลขจากตรงนี้ ไม่ดึงจากข้อความ LLM)
        "rows": [row(r, dates) for r in (doc.get("analysis") or {}).get("analysis", [])],
    }


@router.get("/{patient_id}/ai/analysis")
def ai_analysis(patient_id: str, response: Response, doctor_id: str = Depends(current_doctor)):
    subject, dates = ai_patient(patient_id, doctor_id, response)
    doc, source, saved_at = fetch_or_stored(patient_id, "analysis", "analysis", subject=subject)
    rows = [row(r, dates) for r in doc.get("analysis", []) if r.get("subject_id") == subject]
    days = sorted({(r["day"], r["date"]) for r in rows})
    return {"patient_id": patient_id, **meta(doc), "source": source, "saved_at": saved_at,
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
    is_llm = lambda doc: (doc.get("explanation") or {}).get("generation_kind") == "llm"
    doc, source, saved_at = fetch_or_stored(patient_id, f"explanation#{date}", "explanation", keep=is_llm,
                                            subject=subject, day=by_date[date])
    return explanation_out(patient_id, date, by_date[date], doc, dates, source, saved_at)


@router.get("/{patient_id}/ai/explanations")
def ai_saved_explanations(patient_id: str, response: Response, doctor_id: str = Depends(current_doctor)):
    """คำอธิบายทุกวันที่เก็บไว้แล้ว (ไม่เรียกเดโม ไม่กินโควตา) หน้ารายงานแสดงได้ทันทีโดยไม่ต้องกด"""
    _, dates = ai_patient(patient_id, doctor_id, response)
    by_date = {dt: d for d, dt in dates.items()}
    items = query_all(table("ai_results"), KeyConditionExpression=Key("patient_id").eq(patient_id)
                      & Key("result_key").begins_with("explanation#"))
    out = []
    for it in items:
        date = it["result_key"].split("#", 1)[1]
        if date in by_date:
            out.append(explanation_out(patient_id, date, by_date[date], json.loads(it["doc"]), dates,
                                       "saved", it.get("saved_at")))
    return {"patient_id": patient_id, "items": sorted(out, key=lambda x: x["date"])}

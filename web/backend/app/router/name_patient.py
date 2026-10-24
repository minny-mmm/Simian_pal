"""หน้า 2 รายชื่อผู้ป่วย"""
from datetime import date, timedelta
from typing import Literal, Optional

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Depends, Query

from app.auth import current_doctor
from app.config import PAGE_SIZE, WEAR_HOURS
from app.db import query_all, table, to_json
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query   # เพิ่ม HTTPException
from pydantic import BaseModel, Field
from app import metrics  

router = APIRouter(prefix="/api/patients", tags=["patients"])

# แท็บด้านบนตาราง: ชื่อแท็บ -> เงื่อนไขว่าแถวไหนเข้าแท็บนั้น
FILTERS = {
    "all": lambda r: True,
    "wearing": lambda r: r["wearing"],
    "not_wearing": lambda r: not r["wearing"],
    "follow_up": lambda r: r["follow_up"],
}


def patient_row(p, start=None, end=None):
    """1 แถวในตาราง: ชม.ใส่เฉลี่ยในช่วงวันที่ (เกณฑ์เดียวกับการ์ดหน้า 1) + แนวโน้ม 7 วันสุดท้ายของช่วง
    ไม่ระบุช่วง = 7 วันล่าสุดที่มีข้อมูลของผู้ป่วยคนนี้ (ช่วงเดียวกับหน้าโปรไฟล์)"""
    key = Key("patient_id").eq(p["patient_id"])
    if end is None:
        days = query_all(table("daily_metrics"), KeyConditionExpression=key)
        end = max((d["date"] for d in days), default=date.today().isoformat())
        start = (date.fromisoformat(end) - timedelta(days=6)).isoformat()
        days = [d for d in days if start <= d["date"] <= end]
    else:
        days = query_all(table("daily_metrics"), KeyConditionExpression=key & Key("date").between(start, end))
    by_date = {d["date"]: d.get("wear_hours") for d in days}
    hours = [h for h in by_date.values() if h is not None]
    avg = round(sum(hours) / len(hours), 1) if hours else None
    # 7 ค่าเรียงตามวัน วันที่ไม่มีข้อมูล = None (กราฟจะเว้นช่องไว้)
    last = date.fromisoformat(end)
    trend = [by_date.get((last - timedelta(days=6 - i)).isoformat()) for i in range(7)]
    return {
        "patient_id": p["patient_id"],
        "hn": p.get("hn"),
        "display_name": p.get("display_name"),
        "age": int(end[:4]) - int(p["birth_year"]) if p.get("birth_year") else None,
        "wear_hours": avg,
        "wearing": avg is not None and avg >= WEAR_HOURS,
        "follow_up": bool(p.get("follow_up")),
        "has_ai": bool(p.get("ai_subject")),   # มีผลวิเคราะห์จากเดโม AI (หน้ารายงาน)
        "trend_7d": trend,
        "data_from": start, "data_to": end,   # ช่วงที่ใช้คิดชม.ใส่ของแถวนี้
    }


@router.get("")
def list_patients(tab: Literal["all", "wearing", "not_wearing", "follow_up"] = Query("all", alias="filter"),
                  search: Optional[str] = None,
                  page: int = Query(1, ge=1),
                  start: Optional[str] = Query(None, alias="from"),
                  end: Optional[str] = Query(None, alias="to"),
                  doctor_id: str = Depends(current_doctor)):
    if start or end:   # เลือกช่วงมา: ใช้ช่วงเดียวกันทุกคน
        end = end or date.today().isoformat()
        start = start or (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    # ไม่เลือกช่วง: start/end = None -> แต่ละคนใช้ 7 วันล่าสุดที่มีข้อมูลของตัวเอง
    patients = query_all(table("patients"), IndexName="by_doctor",
                         KeyConditionExpression=Key("doctor_id").eq(doctor_id))

    if search:  # ช่องค้นหา: หาใน HN หรือชื่อ
        s = search.lower()
        patients = [p for p in patients
                    if s in p.get("hn", "").lower() or s in p.get("display_name", "").lower()]

    rows = [patient_row(p, start, end) for p in sorted(patients, key=lambda p: p.get("hn", ""))]
    counts = {name: sum(1 for r in rows if rule(r)) for name, rule in FILTERS.items()}

    rows = [r for r in rows if FILTERS[tab](r)]
    total = len(rows)
    rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]   # ตัดเอาเฉพาะหน้าที่ขอ

    return to_json({"from": start, "to": end, "counts": counts, "total": total, "page": page,
                    "page_size": PAGE_SIZE, "items": rows})

class NewPatient(BaseModel):
    """หน้าตาของข้อมูลที่ฟอร์ม "เพิ่มผู้ป่วย" ต้องส่งมา"""
    patient_id: str = Field(min_length=1, examples=["MG-011"])
    hn: str = Field(min_length=1, examples=["HN-000011"])
    display_name: str = Field(min_length=1, examples=["นาย สรวิทธิ์ ค.***"])
    sex: Literal["M", "F"]
    birth_year: int = Field(ge=1900, le=2026)
    status: Literal["PD", "สงสัย PD", "ปกติ"] = "PD"
    watch_wrist: Literal["left", "right"] = "left"
    follow_up: bool = False


@router.post("", status_code=201)
def add_patient(body: NewPatient, doctor_id: str = Depends(current_doctor)):
    item = {
        **body.model_dump(),                         # ข้อมูลจากฟอร์มทั้งหมด
        "doctor_id": doctor_id,                      # ผูกกับหมอที่ login (ไม่ให้ฟอร์มส่งมาเอง)
        "monitoring_start": date.today().isoformat(),
        "is_demo": True,
    }
    try:
        # ConditionExpression = เขียนได้เฉพาะตอนที่ยังไม่มี patient_id นี้ (กันเขียนทับคนเดิม)
        table("patients").put_item(Item=item,
                                   ConditionExpression="attribute_not_exists(patient_id)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail="มีรหัสผู้ป่วยนี้อยู่แล้ว")
        raise
    return item

def get_own_patient(patient_id, doctor_id):
    """ดึงผู้ป่วย 1 คน ถ้าไม่มี หรือเป็นผู้ป่วยของหมอคนอื่น ตอบ 404"""
    p = table("patients").get_item(Key={"patient_id": patient_id}).get("Item")
    if not p or p.get("doctor_id") != doctor_id:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ป่วย")
    return p


def daily_rows(patient_id):
    """ค่ารายวันทั้งหมดของผู้ป่วย (แปลง Decimal เป็นตัวเลขปกติก่อนคำนวณ)"""
    return to_json(query_all(table("daily_metrics"),
                             KeyConditionExpression=Key("patient_id").eq(patient_id)))


@router.get("/{patient_id}")
def get_patient(patient_id: str, end: Optional[str] = None,
                doctor_id: str = Depends(current_doctor)):
    p = get_own_patient(patient_id, doctor_id)
    # ไม่ระบุ end = วันล่าสุดที่มีข้อมูล (ช่วงเดียวกับการ์ด /summary)
    end = end or max((r["date"] for r in daily_rows(patient_id)), default=date.today().isoformat())
    start = (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    return to_json({**p, **patient_row(p, start, end)})   # ข้อมูลทั่วไป + ชม.ใส่/สถานะ 7 วัน


@router.get("/{patient_id}/summary")
def get_summary(patient_id: str, days: int = Query(7, ge=1, le=90),
                start: Optional[str] = Query(None, alias="from"),
                end: Optional[str] = Query(None, alias="to"),
                doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)
    if start and end:   # เลือกช่วงวันที่มา (หน้า 4): ใช้ช่วงนั้น เทียบกับช่วงยาวเท่ากันก่อนหน้า
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return metrics.summary(daily_rows(patient_id), days, end if start and end else None)


@router.get("/{patient_id}/metrics")
def get_metric(patient_id: str,
               metric: Literal["tremor", "cadence", "activity"] = "tremor",
               days: int = Query(7, ge=1, le=90),   # ปุ่ม 7 / 30 / 90 วัน (Literal ตัวเลขรับค่าจาก URL ไม่ได้)
               doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)
    return metrics.series(daily_rows(patient_id), metric, days)
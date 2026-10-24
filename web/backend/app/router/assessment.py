"""หน้า 5 การประเมินโดยแพทย์ (MDS-UPDRS / MoCA / CDR)"""
from datetime import date
from typing import Dict, Literal, Optional

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import current_doctor
from app.db import query_all, table, to_db, to_json
from app.router.doctor import doctor_names
from app.router.name_patient import get_own_patient

router = APIRouter(prefix="/api/patients", tags=["assessments"])

TestType = Literal["MDS-UPDRS", "MoCA", "CDR", "UPDRS-III-OFF"]
# MDS-UPDRS Part III = 33 ข้อ x 4 คะแนน · UPDRS-III-OFF = UPDRS ฉบับเดิม Part III (27 ข้อ x 4) ตรวจตอนไม่ได้กินยา
MAX_SCORE = {"MDS-UPDRS": 132, "MoCA": 30, "CDR": 3, "UPDRS-III-OFF": 108}


@router.get("/{patient_id}/assessments")
def list_assessments(patient_id: str,
                     test: TestType = Query("MDS-UPDRS", alias="type"),
                     doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)
    # begins_with: เอาเฉพาะ sort key ที่ขึ้นต้นด้วย "MDS-UPDRS#" -> ได้แค่แบบประเมินที่เลือก
    rows = to_json(query_all(
        table("assessments"),
        KeyConditionExpression=Key("patient_id").eq(patient_id)
        & Key("type_date").begins_with(test + "#"),
        ScanIndexForward=False))          # ใหม่ -> เก่า (วันที่อยู่ใน sort key)
    names = doctor_names(r.get("assessor_id") for r in rows)
    for r in rows:   # ช่อง "ผู้ประเมิน" แสดงชื่อหมอแทนรหัส
        r["assessor_name"] = names.get(r.get("assessor_id"))
    return {"type": test, "max": MAX_SCORE[test],
            "latest": rows[0] if rows else None,   # การ์ดคะแนนใหญ่ + รายละเอียดรายข้อ
            "history": rows}                       # ตารางประวัติ


class NewAssessment(BaseModel):
    """ข้อมูลจากฟอร์ม "บันทึกผลการประเมินใหม่" """
    type: TestType
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", examples=["2026-10-24"])
    items: Dict[str, float] = Field(default_factory=dict,
                                    examples=[{"rest_tremor": 2, "bradykinesia": 3}])
    total: Optional[float] = None     # ไม่ส่งมา = รวมจาก items ให้
    med_state: Optional[Literal["OFF", "ON"]] = None   # ประเมินก่อน/หลังกินยา
    note: Optional[str] = None


@router.post("/{patient_id}/assessments", status_code=201)
def add_assessment(patient_id: str, body: NewAssessment,
                   doctor_id: str = Depends(current_doctor)):
    get_own_patient(patient_id, doctor_id)

    # เช็กเงื่อนไขที่ Pydantic เช็กให้ไม่ได้ (ต้องดูหลายช่องประกอบกัน)
    if body.date > date.today().isoformat():
        raise HTTPException(status_code=422, detail="วันที่ประเมินต้องไม่เป็นวันในอนาคต")
    total = body.total if body.total is not None else sum(body.items.values())
    if not 0 <= total <= MAX_SCORE[body.type]:
        raise HTTPException(status_code=422,
                            detail=f"คะแนนรวม {body.type} ต้องอยู่ระหว่าง 0–{MAX_SCORE[body.type]}")

    item = {
        "patient_id": patient_id,
        "type_date": f"{body.type}#{body.date}",
        "type": body.type,
        "date": body.date,
        "total": total,
        "max": MAX_SCORE[body.type],
        "items": body.items or None,
        "med_state": body.med_state,
        "note": body.note,
        "assessor_id": doctor_id,      # คนประเมิน = หมอที่ login ไม่รับจากฟอร์ม
    }
    try:
        table("assessments").put_item(Item=to_db(item),
                                      ConditionExpression="attribute_not_exists(type_date)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409,
                                detail=f"วันที่ {body.date} บันทึก {body.type} ของผู้ป่วยนี้ไว้แล้ว")
        raise
    return to_json(to_db(item))
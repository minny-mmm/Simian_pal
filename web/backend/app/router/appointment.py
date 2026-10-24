"""หน้า 8 นัดหมาย"""
from collections import Counter
from typing import Literal, Optional

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import current_doctor
from app.db import query_all, table, to_db, to_json
from app.router.name_patient import get_own_patient

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

Status = Literal["มาแล้ว", "รอพบ", "ยังไม่มา"]
DATE = r"^\d{4}-\d{2}-\d{2}$"
TIME = r"^([01]\d|2[0-3]):[0-5]\d$"      # 00:00 – 23:59


@router.get("")
def list_appointments(month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
                      day: Optional[str] = Query(None, alias="date", pattern=DATE),
                      doctor_id: str = Depends(current_doctor)):
    if bool(month) == bool(day):
        raise HTTPException(status_code=422, detail="ส่ง month หรือ date มาอย่างใดอย่างหนึ่ง")

    # slot ขึ้นต้นด้วยวันที่ -> ใช้ begins_with ดึงทั้งเดือน ("2026-10-") หรือทั้งวัน ("2026-10-22#")
    prefix = day + "#" if day else month + "-"
    items = to_json(query_all(table("appointments"),
                              KeyConditionExpression=Key("doctor_id").eq(doctor_id)
                              & Key("slot").begins_with(prefix)))

    if month:   # ปฏิทิน: แค่บอกว่าวันไหนมีกี่นัด ไม่ต้องส่งรายละเอียด
        return {"month": month, "days": dict(Counter(a["date"] for a in items))}

    # รายการนัดของวัน: เติม HN + ชื่อผู้ป่วยให้หน้าเว็บแสดงได้เลย (เรียงตามเวลาอยู่แล้วเพราะ slot)
    patients = {p["patient_id"]: p for p in query_all(
        table("patients"), IndexName="by_doctor",
        KeyConditionExpression=Key("doctor_id").eq(doctor_id))}
    for a in items:
        p = patients.get(a["patient_id"], {})
        a["hn"], a["display_name"] = p.get("hn"), p.get("display_name")
    return {"date": day, "items": items}


class NewAppointment(BaseModel):
    patient_id: str = Field(min_length=1, examples=["DEMO-002"])
    date: str = Field(pattern=DATE, examples=["2026-11-12"])
    time: str = Field(pattern=TIME, examples=["14:30"])
    purpose: str = Field(min_length=1, examples=["ติดตามอาการ"])
    status: Status = "ยังไม่มา"
    note: Optional[str] = None


@router.post("", status_code=201)
def add_appointment(body: NewAppointment, doctor_id: str = Depends(current_doctor)):
    get_own_patient(body.patient_id, doctor_id)     # นัดได้เฉพาะผู้ป่วยของตัวเอง
    item = {**body.model_dump(),
            "doctor_id": doctor_id,
            "slot": f"{body.date}#{body.time}#{body.patient_id}"}
    try:
        table("appointments").put_item(Item=to_db(item),
                                       ConditionExpression="attribute_not_exists(slot)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail="ผู้ป่วยคนนี้มีนัดเวลานี้อยู่แล้ว")
        raise
    return to_json(to_db(item))


class AppointmentUpdate(BaseModel):
    status: Status


@router.patch("/{date}/{time}/{patient_id}")
def update_appointment(date: str, time: str, patient_id: str, body: AppointmentUpdate,
                       doctor_id: str = Depends(current_doctor)):
    try:
        r = table("appointments").update_item(
            Key={"doctor_id": doctor_id, "slot": f"{date}#{time}#{patient_id}"},
            UpdateExpression="SET #st = :st",
            ExpressionAttributeNames={"#st": "status"},       # status เป็นคำสงวน (ขั้น 10)
            ExpressionAttributeValues={":st": body.status},
            ConditionExpression="attribute_exists(slot)",     # ไม่มีนัดนี้ = อย่าสร้างแถวใหม่ขึ้นมาเอง
            ReturnValues="ALL_NEW")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=404, detail="ไม่พบนัดหมายนี้")
        raise
    return to_json(r["Attributes"])
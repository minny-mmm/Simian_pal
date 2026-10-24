"""ต่อ DynamoDB + ตัวช่วยที่ทุก router ใช้"""
import os
from decimal import Decimal

import boto3

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
PREFIX = os.environ.get("TABLE_PREFIX", "pd_")


def table(name):
    """table("patients") -> ตาราง pd_patients"""
    return boto3.resource("dynamodb", region_name=REGION).Table(PREFIX + name)


def to_json(x):
    """DynamoDB คืนตัวเลขเป็น Decimal ต้องแปลงก่อนส่งเป็น JSON"""
    if isinstance(x, list):
        return [to_json(v) for v in x]
    if isinstance(x, dict):
        return {k: to_json(v) for k, v in x.items()}
    if isinstance(x, Decimal):
        return int(x) if x == x.to_integral_value() else float(x)
    return x


def query_all(tbl, **kwargs):
    """query แล้วดึงต่อจนครบ (DynamoDB ส่งมาทีละไม่เกิน 1 MB)"""
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        r = tbl.query(**kwargs)
        items += r["Items"]
        start = r.get("LastEvaluatedKey")
        if not start:
            return items

def to_db(x):
    """ตรงข้ามกับ to_json: float -> Decimal ก่อนเขียนลง DynamoDB และตัดค่า None ทิ้ง"""
    if isinstance(x, list):
        return [to_db(v) for v in x]
    if isinstance(x, dict):
        return {k: to_db(v) for k, v in x.items() if v is not None}
    if isinstance(x, float):
        return Decimal(str(x))   # ผ่าน str ก่อน 0.1 จะได้ไม่กลายเป็น 0.1000000000000000055...
    return x
"""สร้างตาราง / โหลดข้อมูลจาก data.py / เช็กข้อมูลใน DynamoDB

ก่อนใช้: pip install boto3 pandas  แล้ว  aws configure (region ap-southeast-1)

python load_dynamodb.py create                 สร้างตารางที่ยังไม่มี
python load_dynamodb.py load                   โหลดทุกตารางใน data.TABLE_DATA
python load_dynamodb.py load pd_daily_metrics  โหลดตารางเดียว
python load_dynamodb.py check                  นับแถวทุกตาราง + โชว์ค่ารายวันวันแรกของผู้ป่วยคนแรก
"""
import math
import os
import sys
from decimal import Decimal

import boto3

from data import PATIENTS, START_DATE, TABLE_DATA

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")

# ตาราง: (partition key, sort key หรือ None, index) ชนิด S = ข้อความ, N = ตัวเลข
TABLES = {
    "pd_doctors":         (("doctor_id", "S"), None, None),
    "pd_patients":        (("patient_id", "S"), None, ("by_doctor", ("doctor_id", "S"), None)),
    "pd_daily_metrics":   (("patient_id", "S"), ("date", "S"), None),
    "pd_reference":       (("metric", "S"), ("cohort", "S"), None),
    "pd_alerts":          (("patient_id", "S"), ("created_at", "S"),
                           ("by_doctor", ("doctor_id", "S"), ("created_at", "S"))),
    "pd_notes":           (("patient_id", "S"), ("date", "S"), None),
    "pd_devices":         (("patient_id", "S"), None, None),
    "pd_assessments":     (("patient_id", "S"), ("type_date", "S"), None),
    "pd_appointments":    (("doctor_id", "S"), ("slot", "S"), None),
    "pd_hourly_features": (("subject_id", "S"), ("hour_idx", "N"), None),
}
# 10 ตาราง + 2 index x 2 = 24 อยู่ในโควตาฟรี 25
CAP = {"ReadCapacityUnits": 2, "WriteCapacityUnits": 2}

db = boto3.resource("dynamodb", region_name=REGION)


def key_schema(pk, sk):
    return ([{"AttributeName": pk[0], "KeyType": "HASH"}]
            + ([{"AttributeName": sk[0], "KeyType": "RANGE"}] if sk else []))


def create():
    existing = {t.name for t in db.tables.all()}
    for name, (pk, sk, gsi) in TABLES.items():
        if name in existing:
            print(f"มีแล้ว    {name}")
            continue
        attrs = dict([pk] + ([sk] if sk else []))
        kwargs = {"TableName": name, "KeySchema": key_schema(pk, sk), "ProvisionedThroughput": CAP}
        if gsi:
            gname, gpk, gsk = gsi
            attrs.update(dict([gpk] + ([gsk] if gsk else [])))
            kwargs["GlobalSecondaryIndexes"] = [{
                "IndexName": gname, "KeySchema": key_schema(gpk, gsk),
                "Projection": {"ProjectionType": "ALL"}, "ProvisionedThroughput": CAP}]
        kwargs["AttributeDefinitions"] = [{"AttributeName": a, "AttributeType": t} for a, t in attrs.items()]
        db.create_table(**kwargs).wait_until_exists()
        print(f"สร้างแล้ว {name}")


def clean(x):
    """แปลงค่าให้ DynamoDB รับได้: ตัด None/NaN, float -> Decimal, ตัวเลข numpy -> python"""
    if hasattr(x, "item") and not isinstance(x, (list, dict, str)):
        x = x.item()  # numpy.int64 / numpy.float64
    if isinstance(x, dict):
        out = {k: clean(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v is not None}
    if isinstance(x, (list, tuple)):
        return [v for v in (clean(v) for v in x) if v is not None]
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else Decimal(str(x))
    return x


def load(only=None):
    for name, rows in TABLE_DATA.items():
        if only and name != only:
            continue
        rows = rows() if callable(rows) else rows
        if not rows:
            print(f"ข้าม     {name} (ไม่มีข้อมูล)")
            continue
        pk, sk, _ = TABLES[name]
        keys = [pk[0]] + ([sk[0]] if sk else [])
        with db.Table(name).batch_writer(overwrite_by_pkeys=keys) as batch:
            for r in rows:
                item = clean(r)
                missing = [k for k in keys if k not in item]
                if missing:
                    raise ValueError(f"{name}: แถวนี้ไม่มี key {missing}: {r}")
                batch.put_item(Item=item)
        print(f"โหลดแล้ว  {name}: {len(rows)} แถว")


def check():
    client = db.meta.client
    for name in TABLES:
        try:
            n, kw = 0, {"Select": "COUNT"}
            while True:  # scan ได้ทีละไม่เกิน 1 MB ต้องวนจนครบ
                page = db.Table(name).scan(**kw)
                n += page["Count"]
                if "LastEvaluatedKey" not in page:
                    break
                kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
            print(f"{name:<20} {n:>5} แถว")
        except client.exceptions.ResourceNotFoundException:
            print(f"{name:<20} ยังไม่มีตาราง (รัน create ก่อน)")
    pid, day = PATIENTS[0]["patient_id"], START_DATE.isoformat()
    try:
        item = db.Table("pd_daily_metrics").get_item(Key={"patient_id": pid, "date": day}).get("Item")
        print(f"\n{pid} วันที่ {day}:", item or "ไม่พบ")
    except client.exceptions.ResourceNotFoundException:
        pass
    except client.exceptions.ClientError as e:  # เช่น ตารางบน AWS ใช้ key คนละแบบกับ TABLES
        print(f"\nอ่าน pd_daily_metrics ไม่ได้: {e.response['Error']['Message']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create":
        create()
    elif cmd == "load":
        load(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "check":
        check()
    else:
        print(__doc__)

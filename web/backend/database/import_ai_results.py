"""ดึงผลที่เดโม AI ของทีม (pd-reference-demo-v1) เก็บไว้ใน S3 มาใส่ pd_ai_results — ไม่เรียก API เดโม ไม่กินโควตา

  cd web/backend/database
  AWS_PROFILE=minny python import_ai_results.py          # ดูว่าจะเขียนอะไร (ไม่เขียน)
  AWS_PROFILE=minny python import_ai_results.py --write  # เขียนลง DynamoDB (ตารางต้องมีแล้ว: load_dynamodb.py create)

อ่านอย่างเดียวจากของเพื่อน (ไม่แก้/ไม่ลบ):
- analysis   = handoffs/<ล่าสุด>/analysis_DEPLOYED_REAL.json (ผลที่เดโมส่งออกจริง)
- คำอธิบาย  = cache/<hash>.json ที่เดโมบันทึกหลังเรียก LLM สำเร็จ (ข้าม *.attempt.json / *.REJECTED_*.json)
เก็บเฉพาะของที่ใช้กับเดโมรุ่นปัจจุบัน: baseline_version ตรงกับ analysis, คำอธิบายเป็น LLM (ไม่เอา template)
และตัวเลขใน evidence ตรงกับ analysis ทุกตัว · ผู้ป่วยตาม ai_subject ใน data.py, day -> วันที่ตาม source_day

รูปแบบแถว (app/router/ai.py อ่านแบบนี้): patient_id, result_key, doc = JSON ตามที่ API เดโมตอบทุกตัวอักษร
- result_key "analysis"               doc = {...metadata, analysis: [แถวของผู้ป่วยคนนี้]}
- result_key "explanation#2026-10-04" doc = {analysis: {...metadata, analysis: [แถววันนั้น]}, explanation, validation}
"""
import json
import sys
from datetime import datetime, timezone

import boto3

from data import DAILY_METRICS, PATIENTS
from load_dynamodb import REGION, db

BUCKET = "amzn-a3-simianpal-205506434226-ap-southeast-1-an"
PREFIX = "pd-pipeline/deployments/reference-demo-v1/"
META = ("schema_version", "is_demo", "data_origin", "data_kind", "baseline_version")
s3 = boto3.client("s3", region_name=REGION)


def read(key):
    return json.loads(s3.get_object(Bucket=BUCKET, Key=PREFIX + key)["Body"].read())


def keys(prefix):
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=PREFIX + prefix):
        for o in page.get("Contents", []):
            yield o["Key"][len(PREFIX):], o["LastModified"]


def rows():
    handoff = max(k for k, _ in keys("handoffs/") if k.endswith("/analysis_DEPLOYED_REAL.json"))
    analysis = read(handoff)
    meta = {k: analysis.get(k) for k in META}
    by_alias = {p["ai_subject"]: p["patient_id"] for p in PATIENTS if p.get("ai_subject")}
    dates = {(r["patient_id"], r["source_day"]): r["date"] for r in DAILY_METRICS}
    index = {(r["subject_id"], r["day"], r["metric"]): r for r in analysis["analysis"]}
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = []
    for alias, pid in by_alias.items():
        mine = [r for r in analysis["analysis"] if r["subject_id"] == alias]
        out.append({"patient_id": pid, "result_key": "analysis", "doc": {**meta, "analysis": mine},
                    "source": "s3:" + handoff, "saved_at": saved_at})

    for key, modified in keys("cache/"):
        if key.count(".json") != 1 or not key.endswith(".json"):
            continue   # *.attempt.json / *.REJECTED_*.json = บันทึกระหว่างทาง ไม่ใช่ผลที่เดโมส่ง
        cached = read(key)
        exp = cached.get("explanation") or {}
        items = exp.get("explanations") or []
        subjects = {(e["subject_id"], e["day"]) for e in items}
        if exp.get("generation_kind") != "llm" or len(subjects) != 1:
            continue
        (alias, day), = subjects
        pid, date = by_alias.get(alias), dates.get((by_alias.get(alias), day))
        if not pid or not date or exp.get("baseline_version") != meta["baseline_version"]:
            continue
        # ตัวเลขที่ LLM อ้างต้องตรงกับ analysis ปัจจุบัน
        if any(e["evidence"].get("value") != index[(alias, day, e["metric"])].get("value") for e in items):
            print(f"ข้าม {key}: evidence ไม่ตรง analysis")
            continue
        day_rows = [r for r in analysis["analysis"] if r["subject_id"] == alias and r["day"] == day]
        out.append({"patient_id": pid, "result_key": f"explanation#{date}",
                    "doc": {"analysis": {**meta, "analysis": day_rows}, **cached},
                    "source": "s3:" + key, "saved_at": modified.isoformat(timespec="seconds"),
                    "generation_kind": "llm"})
    return out


if __name__ == "__main__":
    found = rows()
    for r in found:
        print(f"{r['patient_id']}  {r['result_key']:<24} <- {r['source']}")
    if "--write" not in sys.argv:
        print(f"\n{len(found)} แถว (ยังไม่เขียน: เพิ่ม --write)")
        sys.exit()
    with db.Table("pd_ai_results").batch_writer(overwrite_by_pkeys=["patient_id", "result_key"]) as batch:
        for r in found:
            batch.put_item(Item={**r, "doc": json.dumps(r["doc"], ensure_ascii=False)})
    print(f"\nเขียนแล้ว pd_ai_results: {len(found)} แถว")

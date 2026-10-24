"""ตรวจ app/router/ai.py + app/reference.py: แปลง day -> วันที่, error ของเดโม, ไม่ส่งรหัสออก, เก็บผลใน pd_ai_results
(แทนเดโมและ DynamoDB ด้วยของปลอม ไม่ยิงเดโมจริง ไม่กินโควตา)

รัน: cd web/backend && python -m pytest tests -q
"""
import json
import urllib.error

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, reference
from app.router import ai

TOKEN = "fake-demo-token"
PATIENTS = {
    "Patient-001": {"patient_id": "Patient-001", "doctor_id": "DR-001", "ai_subject": "VALID-A"},
    "Patient-003": {"patient_id": "Patient-003", "doctor_id": "DR-001"},
    "Patient-999": {"patient_id": "Patient-999", "doctor_id": "DR-002", "ai_subject": "VALID-B"},
}
DAILY = [{"date": f"2026-10-0{i}", "source_day": i + 2} for i in range(1, 5)]   # 1 ต.ค. = day 3


def analysis_row(day, metric="rest_tremor_window_fraction", value=0.12):
    ref = {"status": "ok", "reason": "", "reference_median": 0.05, "signed_distance": value - 0.05,
           "standardized_deviation": 1.2, "reference_quantiles": {"0.5": 0.05}}
    return {"subject_id": "VALID-A", "dataset": "cops", "split": "valid", "day": day, "metric": metric,
            "unit": "fraction", "value": value, "status": "ok", "qc": {"status": "ok", "reasons": []},
            "reference_comparisons": {"cops": ref, "nhanes": ref},
            "previous_day": {"day": day - 1, "value": 0.1, "delta": 0.02, "status": "ok", "reason": ""}}


ANALYSIS = {"schema_version": "1.0.0", "baseline_version": "reference-baseline-v1", "data_origin": "real",
            "analysis": [analysis_row(d) for d in (3, 4, 5, 6)]}


class FakeDemo:
    """แทน reference.fetch: จำ URL/รหัสที่ถูกเรียก และตอบตามที่ตั้งไว้"""
    def __init__(self, status=200, body=None, error=None):
        self.status, self.body, self.error, self.calls = status, body, error, []

    def __call__(self, url, token):
        self.calls.append((url, token))
        if self.error:
            raise self.error
        if self.body is not None:
            return self.status, self.body
        if "/api/explanation" in url:
            day = int(url.split("day=")[1])
            return 200, {"analysis": {**ANALYSIS, "analysis": [analysis_row(day)]},
                         "explanation": {"schema_version": "1.0.0", "generation_kind": "llm", "model_version": "nova",
                                         "explanations": [{"metric": "rest_tremor_window_fraction", "unit": "fraction",
                                                           "text": "สูงกว่ากลุ่มอ้างอิง", "evidence": {"value": 0.12}}]},
                         "validation": {"generation_status": "llm"}}
        return 200, ANALYSIS


class FakeTable:
    """แทน pd_ai_results (ผู้ป่วยคนเดียวพอสำหรับ test)"""
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["patient_id"], Key["result_key"]))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[(Item["patient_id"], Item["result_key"])] = Item


def own_patient(patient_id, doctor_id):
    p = PATIENTS.get(patient_id)
    if not p or p["doctor_id"] != doctor_id:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ป่วย")
    return p


@pytest.fixture
def demo(monkeypatch):
    fake = FakeDemo()
    monkeypatch.setattr(reference, "BASE_URL", "https://demo.example")
    monkeypatch.setenv("REFERENCE_DEMO_TOKEN", TOKEN)
    monkeypatch.setattr(reference, "fetch", fake)
    monkeypatch.setattr(reference, "_cache", {})
    reference.demo_token.cache_clear()
    monkeypatch.setattr(ai, "get_own_patient", own_patient)
    monkeypatch.setattr(ai, "daily_rows", lambda pid: DAILY)
    fake.db = FakeTable()
    monkeypatch.setattr(ai, "table", lambda name: fake.db)
    monkeypatch.setattr(ai, "query_all", lambda tbl, **kw: [v for (pid, k), v in tbl.items.items()
                                                            if k.startswith("explanation#")])
    return fake


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ai.router)
    app.dependency_overrides[auth.current_doctor] = lambda: "DR-001"
    return TestClient(app)


def test_analysis_maps_demo_day_to_our_date(client, demo):
    r = client.get("/api/patients/Patient-001/ai/analysis")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert [(d["day"], d["date"], d["display_day"]) for d in j["days"]][0] == (3, "2026-10-01", 4)
    r0 = j["rows"][0]
    assert r0["key"] == "tremor" and r0["date"] == "2026-10-01" and r0["display_day"] == 4
    # ส่งต่อทุก field ของเดโม (หน้ารายงานใช้ quantiles / qc)
    assert r0["reference_comparisons"]["cops"]["signed_distance"] == pytest.approx(0.07)
    assert r0["reference_comparisons"]["cops"]["reference_quantiles"] == {"0.5": 0.05}
    assert demo.calls[0] == ("https://demo.example/api/analysis?subject=VALID-A", TOKEN)


def test_analysis_is_cached(client, demo):
    client.get("/api/patients/Patient-001/ai/analysis")
    client.get("/api/patients/Patient-001/ai/analysis")
    assert len(demo.calls) == 1   # ครั้งที่ 2 ไม่กินโควตาเดโม


def test_explanation_uses_source_day(client, demo):
    r = client.get("/api/patients/Patient-001/ai/explanation", params={"date": "2026-10-02"})
    j = r.json()
    assert r.status_code == 200 and j["day"] == 4 and j["label"] == "LLM"
    assert j["explanations"][0]["key"] == "tremor" and j["rows"][0]["date"] == "2026-10-02"
    assert demo.calls[0][0].endswith("/api/explanation?subject=VALID-A&day=4")


def test_explanation_default_is_latest_day(client, demo):
    assert client.get("/api/patients/Patient-001/ai/explanation").json()["date"] == "2026-10-04"


def test_template_fallback_label(client, demo):
    demo.body = {"analysis": ANALYSIS, "explanation": {"generation_kind": "expected_template", "explanations": []},
                 "validation": {"reason": "OverflowError"}}
    j = client.get("/api/patients/Patient-001/ai/explanation").json()
    assert j["label"] == "Template fallback" and j["fallback_reason"] == "OverflowError"


@pytest.mark.parametrize("pid, date", [
    ("Patient-003", None),           # ไม่มี ai_subject -> ไม่เรียกเดโม
    ("Patient-999", None),           # ผู้ป่วยของหมอคนอื่น
    ("Patient-001", "2026-11-01"),   # วันที่ไม่มีข้อมูล
])
def test_not_found_without_calling_demo(client, demo, pid, date):
    r = client.get(f"/api/patients/{pid}/ai/explanation", params={"date": date} if date else {})
    assert r.status_code == 404 and demo.calls == []


@pytest.mark.parametrize("status, expect", [(401, 502), (410, 410), (429, 429), (404, 404), (503, 503), (500, 503)])
def test_demo_errors_are_mapped(client, demo, status, expect):
    demo.status, demo.body = status, {"error": "x"}
    r = client.get("/api/patients/Patient-001/ai/analysis")
    assert r.status_code == expect and TOKEN not in r.text and "demo.example" not in r.text


def test_timeout(client, demo):
    demo.error = urllib.error.URLError(TimeoutError("timed out"))
    assert client.get("/api/patients/Patient-001/ai/analysis").status_code == 504


def test_errors_are_not_cached(client, demo):
    demo.status, demo.body = 429, {"error": "daily_demo_limit_reached"}
    client.get("/api/patients/Patient-001/ai/analysis")
    demo.status, demo.body = 200, None
    assert client.get("/api/patients/Patient-001/ai/analysis").status_code == 200


def test_not_configured(client, demo, monkeypatch):
    monkeypatch.setattr(reference, "BASE_URL", "")
    assert client.get("/api/patients/Patient-001/ai/analysis").status_code == 503 and demo.calls == []


def test_token_never_in_response(client, demo):
    for path in ("analysis", "explanation"):
        assert TOKEN not in json.dumps(client.get(f"/api/patients/Patient-001/ai/{path}").json())


def test_analysis_saved_then_read_without_demo(client, demo):
    first = client.get("/api/patients/Patient-001/ai/analysis").json()
    reference._cache.clear()   # จำลอง Lambda เริ่มใหม่ (cache ในหน่วยความจำหาย)
    demo.status, demo.body = 429, {"error": "daily_demo_limit_reached"}   # โควตาเต็ม
    r = client.get("/api/patients/Patient-001/ai/analysis")
    assert first["source"] == "demo" and r.status_code == 200 and r.json()["source"] == "saved"
    assert r.json()["rows"] == first["rows"] and len(demo.calls) == 1


def test_llm_explanation_saved_and_listed(client, demo):
    client.get("/api/patients/Patient-001/ai/explanation", params={"date": "2026-10-02"})
    demo.error = AssertionError("ต้องไม่เรียกเดโม")
    again = client.get("/api/patients/Patient-001/ai/explanation", params={"date": "2026-10-02"}).json()
    listed = client.get("/api/patients/Patient-001/ai/explanations").json()["items"]
    assert again["source"] == "saved" and again["label"] == "LLM"
    assert [(x["date"], x["day"], x["source"]) for x in listed] == [("2026-10-02", 4, "saved")]


def test_template_explanation_not_saved(client, demo):
    demo.body = {"analysis": ANALYSIS, "explanation": {"generation_kind": "expected_template", "explanations": []},
                 "validation": {"reason": "OverflowError"}}
    client.get("/api/patients/Patient-001/ai/explanation")
    assert not [k for k in demo.db.items if k[1].startswith("explanation#")]


def test_saved_list_does_not_call_demo(client, demo):
    demo.error = AssertionError("ต้องไม่เรียกเดโม")
    r = client.get("/api/patients/Patient-001/ai/explanations")
    assert r.status_code == 200 and r.json()["items"] == []

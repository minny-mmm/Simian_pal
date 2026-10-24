"""ตรวจ app/metrics.py: ค่ารวมหลายวันแบบถ่วงน้ำหนักตามสูตรของโมเดล + วันล่าสุดบนการ์ด

รัน: cd web/backend && python -m pytest tests -q
"""
import pytest

from app import metrics


def day(d, tremor, n_rest, n_tremor, cad=None, n_walk=0, enmo=0.02, n_windows=4000):
    return {"date": d, "tremor_rest_fraction": tremor, "n_rest": n_rest, "n_tremor": n_tremor,
            "cadence_steps_s": cad, "n_walk": n_walk, "enmo_g": enmo, "n_windows": n_windows}


ROWS = [day("2026-10-01", 0.1, 1000, 100, cad=1.5, n_walk=30),
        day("2026-10-02", 0.2, 100, 20, cad=2.0, n_walk=10, enmo=0.04, n_windows=1000),
        day("2026-10-03", None, 0, 0)]   # วันที่ไม่มีค่า tremor ไม่นับ


def test_tremor_is_sum_over_sum_not_mean_of_days():
    # (100 + 20) / (1000 + 100) = 10.909% ไม่ใช่ค่าเฉลี่ยของ 10% กับ 20% (= 15%)
    assert metrics.pooled(ROWS, "tremor") == pytest.approx(120 / 1100 * 100)


def test_cadence_and_activity_weighted_by_windows():
    assert metrics.pooled(ROWS, "cadence") == pytest.approx((1.5 * 30 + 2.0 * 10) / 40 * 60)
    assert metrics.pooled(ROWS, "activity") == pytest.approx((0.02 * 4000 + 0.04 * 1000 + 0.02 * 4000) / 9000 * 1000)


def test_old_rows_without_counts_use_plain_mean():
    rows = [{"date": "2026-10-01", "tremor_rest_fraction": 0.1}, {"date": "2026-10-02", "tremor_rest_fraction": 0.2}]
    assert metrics.pooled(rows, "tremor") == pytest.approx(15)


def test_summary_card_has_model_metric_and_latest_day():
    s = metrics.summary(ROWS)["tremor"]
    assert s["model_metric"] == "rest_tremor_window_fraction" and s["value"] == 10.91
    assert s["latest"] == {"date": "2026-10-02", "value": 20.0} and s["valid_days"] == 2


def test_no_data_card():
    s = metrics.summary([day("2026-10-01", None, 0, 0, enmo=None)])
    assert s["tremor"]["value"] is None and s["tremor"]["latest"] is None

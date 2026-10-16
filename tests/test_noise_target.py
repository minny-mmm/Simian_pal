"""เป้า noise ต่อแกน (src/clean/noise_target.py): วัดจาก parquet ได้เท่ากับตอน clean และการเทียบกับ config"""
import numpy as np
import pandas as pd

from src.clean import noise_target, run, steps

FS = 50
CONFIG = {"clean": {"resample": {"fs_out": FS},
                    "harmonize": {"noise_window_s": 10, "min_noise_windows": 30,
                                  "add_noise": {"target_mg": [4.9, 5.1, 6.0]}}}}


def fake_res(first_s=37.3, hours=2, sigma_mg=(4.0, 5.0, 6.0), seed=0):
    """ผล clean จำลอง: วางนิ่ง (ติดธง nonwear) ทั้งช่วง เริ่มไม่ตรงขอบหน้าต่าง 10 วิ"""
    first = int(first_s * FS)
    n = first + hours * 3600 * FS
    xyz = np.full((n, 3), np.nan, np.float32)
    rng = np.random.default_rng(seed)
    xyz[first:] = rng.normal(0, 1, (n - first, 3)) * np.array(sigma_mg) / 1000
    xyz[first:, 2] += 1
    flags = np.full(n, steps.BIT["missing"], np.uint16)
    flags[first:] = steps.BIT["nonwear"]
    return dict(fs=FS, xyz=xyz, flags=flags, valid=np.zeros(n, bool),
                visit_h=np.array(["outpatient"] * (n // (3600 * FS) + 1)), first=first, last=n - 1)


def test_axes_from_parquet_matches_clean(tmp_path):
    res = fake_res()
    path = tmp_path / "S.parquet"
    run.write_parquet(path, res)
    axes, nf, nwin, src = noise_target.axes_from_parquet(path, CONFIG)
    mask = (res["flags"] & steps.BIT["nonwear"]) > 0
    want, _ = steps.noise_floor_axes_mg(res["xyz"], FS, 10, mask, 30)
    want_nf, want_n = steps.noise_floor_mg(res["xyz"], FS, 10, mask, 30)
    assert src == "nonwear" and nwin == want_n
    np.testing.assert_allclose(axes, want, rtol=1e-5)
    assert nf == want_nf
    np.testing.assert_allclose(axes, [4, 5, 6], rtol=0.05)


def test_compare_warns_when_config_is_stale():
    df = pd.DataFrame(dict(subject_id=["A", "B", "C"], x=[4.8, 4.9, 5.0], y=[5.1] * 3, z=[6.0] * 3,
                           src=["qc"] * 3))
    computed, configured, warn = noise_target.compare(CONFIG, df, 0.1)
    np.testing.assert_allclose(computed, [4.9, 5.1, 6.0])
    assert warn == []
    stale = {"clean": {"harmonize": {"add_noise": {"target_mg": [4.5, 5.1, 6.0]}}}}
    assert any("ต่างจาก" in w for w in noise_target.compare(stale, df, 0.1)[2])


def test_compare_warns_missing_subjects():
    df = pd.DataFrame(dict(subject_id=["A", "B"], x=[4.9, np.nan], y=[5.1, np.nan], z=[6.0, np.nan],
                           src=["qc", "missing"]))
    _, _, warn = noise_target.compare(CONFIG, df, 0.1)
    assert any("B" in w for w in warn)

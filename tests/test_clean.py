"""unit test ของขั้น clean ด้วยข้อมูลจำลอง (ขั้น 2, 4, 7, 8)"""
import numpy as np
import pytest
from scipy.signal import resample_poly

from src.clean import steps

ISM = {"window_s": 10, "range_max_g": 0.05, "min_valid_frac": 0.5,
       "constant_window_s": 1, "constant_sd_max_g": 0.001}
NONWEAR = {"window_min": 60, "step_min": 15, "sd_max_g": 0.013, "range_max_g": 0.05,
           "range_smooth_s": 1, "min_axes": 2, "min_valid_frac": 0.5}


def xyz_of(t):
    """ค่าที่รู้ล่วงหน้า: x = t, y = 2t, z = 1"""
    return np.c_[t, 2 * t, np.ones_like(t)].astype(np.float32)


def collect(tf, chunks):
    out = []
    for t in chunks:
        out += tf.push(t, xyz_of(t))
    return out


# ---------------------------------------------------------------- ขั้น 2

def test_time_sort_and_dedupe():
    t = np.arange(100) / 100
    shuffled = np.concatenate([t[50:], t[:50], t[10:20]])   # สลับลำดับ + เวลาซ้ำ 10 จุด
    tf = steps.TimeFixer(100, 1.0)
    [(start, vals)] = collect(tf, [shuffled])
    assert start == 0 and len(vals) == 100
    np.testing.assert_allclose(vals[:, 0], t, atol=1e-6)
    assert tf.n_duplicates == 10 and tf.n_unsorted == 1


def test_short_gap_is_interpolated():
    t = np.concatenate([np.arange(0, 100), np.arange(150, 200)]) / 100   # หาย 0.5 วินาที
    tf = steps.TimeFixer(100, 1.0)
    [(start, vals)] = collect(tf, [t])
    assert (start, len(vals)) == (0, 200)
    np.testing.assert_allclose(vals[:, 0], np.arange(200) / 100, atol=1e-6)   # เติมแบบ linear
    assert tf.n_interpolated == 50   # 1.00-1.49 วินาที


def test_long_gap_is_not_filled():
    t = np.concatenate([np.arange(0, 100), np.arange(400, 500)]) / 100   # หาย 3 วินาที
    tf = steps.TimeFixer(100, 1.0)
    runs = collect(tf, [t])
    assert [(s, len(v)) for s, v in runs] == [(0, 100), (400, 100)]
    assert tf.n_long_gaps == 1


def test_contiguous_files_join_but_missing_hour_does_not():
    tf = steps.TimeFixer(100, 1.0)
    a = collect(tf, [np.arange(0, 100) / 100])
    b = collect(tf, [np.arange(100, 200) / 100])            # ไฟล์ถัดไปติดกัน
    c = collect(tf, [np.arange(360000, 360100) / 100])      # ข้ามไป 1 ชม.
    assert [(s, len(v)) for s, v in a + b + c] == [(0, 100), (100, 100), (360000, 100)]


def test_jittered_timestamps_land_on_grid():
    # NHANES เขียนเวลาปัดเป็นมิลลิวินาที (0.000, 0.013, 0.025, ...) ที่ 80 Hz
    t = np.round(np.arange(800) / 80, 3)
    tf = steps.TimeFixer(80, 1.0)
    [(start, vals)] = collect(tf, [t])
    assert (start, len(vals)) == (0, 800)
    np.testing.assert_allclose(vals[:, 0], np.arange(800) / 80, atol=1e-3)


# ---------------------------------------------------------------- ขั้น 4

@pytest.mark.parametrize("fs_in", [80, 100])
def test_chunked_resample_matches_one_shot(fs_in):
    t = np.arange(fs_in * 120) / fs_in
    x = np.c_[np.sin(2 * np.pi * t), np.cos(2 * np.pi * 0.5 * t), np.ones_like(t)].astype(np.float32)
    rs = steps.Resampler(fs_in, 50, chunk_s=10, context_s=5)
    rs.push(0, x[: len(x) // 3])
    rs.push(len(x) // 3, x[len(x) // 3:])          # ต่อกัน = ช่วงเดียว
    out = rs.assemble(align=50)
    ref = resample_poly(x, 50, fs_in, axis=0, padtype="line")[:len(out)]
    # ต่างกันได้เล็กน้อยที่ตัวอย่างแรก (padtype=line ใช้ปลายทั้งสองข้างของก้อน)
    np.testing.assert_allclose(out[:len(ref)], ref, atol=1e-3)


def test_resample_keeps_gaps_and_alignment():
    x = np.ones((1000, 3), np.float32)
    rs = steps.Resampler(80, 50)
    rs.push(3, x)                 # เริ่มที่ index 3 -> เลื่อนไป 8 (หาร 8 ลงตัว)
    rs.push(5000, x)              # ช่วงที่สองแยกกัน
    out = rs.assemble(align=50)
    ok = ~np.isnan(out[:, 0])
    assert ok[8 * 5 // 8] and not ok[0:5].any()
    assert not ok[(1003 * 5) // 8 + 1:5000 * 5 // 8].any()   # ช่องว่างยังเป็นช่องว่าง
    np.testing.assert_allclose(out[ok], 1, atol=1e-3)   # ripple ของ polyphase filter ~0.2 mg


# ---------------------------------------------------------------- ขั้น 7

def still_then_moving(fs=50, seconds=60, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(fs * seconds) / fs
    xyz = np.zeros((len(t), 3), np.float32)
    xyz[:, 2] = 1
    xyz += rng.normal(0, 0.005, xyz.shape).astype(np.float32)
    half = len(t) // 2
    xyz[half:, 0] += 0.3 * np.sin(2 * np.pi * t[half:])
    return xyz


def test_ism_flags_only_still_windows():
    xyz = still_then_moving()
    m = steps.ism_mask(xyz, 50, ISM)
    assert m[:1500].all() and not m[1500:].any()


def test_ism_threshold_comes_from_config():
    xyz = np.zeros((500, 3), np.float32)
    xyz[::2, 0] = 0.03                     # range 30 mg ในแกน x
    xyz[1::2, 0] = -0.03                   # -> range 60 mg
    assert not steps.ism_mask(xyz, 50, ISM).any()
    assert steps.ism_mask(xyz, 50, {**ISM, "range_max_g": 0.07}).all()


def test_ism_catches_idle_mode_inside_window():
    # ขยับ 6 วินาทีแล้วค่าค้าง (idle mode) 4 วินาที: หน้าต่าง 10 วิไม่จับ แต่กฎค่าคงที่ 1 วิจับ
    xyz = still_then_moving(seconds=20)[500:].copy()   # ครึ่งหลัง = ขยับ 10 วิ
    xyz[300:] = xyz[299]
    m = steps.ism_mask(xyz, 50, ISM)
    assert m[300:].all() and not m[:300].any()


def test_ism_ignores_missing_windows():
    xyz = np.full((1000, 3), np.nan, np.float32)
    xyz[500:] = [0, 0, 1]
    m = steps.ism_mask(xyz, 50, ISM)
    assert not m[:500].any() and m[500:].all()


def test_quantize_and_noise_reach_target():
    xyz = np.zeros((50 * 600, 3), np.float32)
    xyz[:, 2] = 1
    steps.quantize(q := xyz.copy(), 0.0039)
    np.testing.assert_allclose(q / 0.0039, np.round(q / 0.0039), atol=1e-3)

    rng = np.random.default_rng(1)
    xyz += rng.normal(0, 0.002, xyz.shape).astype(np.float32)   # noise เดิม 2 mg
    all_true = np.ones(len(xyz), bool)
    before, _ = steps.noise_floor_mg(xyz, 50, 10, all_true, 10)
    assert before == pytest.approx(2, rel=0.1)
    steps.add_noise(xyz, np.sqrt(7 ** 2 - before ** 2) / 1000, seed=0)
    after, _ = steps.noise_floor_mg(xyz, 50, 10, all_true, 10)
    assert after == pytest.approx(7, rel=0.05)


# ---------------------------------------------------------------- ขั้น 8

def wear_pattern(fs=10, still_axes=3):
    """4 ชม.: 0-1.5 ชม. ขยับ, 1.5-3.5 ชม. วางนิ่ง (noise 3 mg), 3.5-4 ชม. ขยับ"""
    rng = np.random.default_rng(2)
    n = 4 * 3600 * fs
    t = np.arange(n) / fs
    xyz = np.zeros((n, 3), np.float32)
    xyz[:, 2] = 1
    xyz += rng.normal(0, 0.003, xyz.shape).astype(np.float32)
    moving = (t < 1.5 * 3600) | (t >= 3.5 * 3600)
    for k in range(3):
        axis_moves = moving if k < still_axes else np.ones(n, bool)
        xyz[axis_moves, k] += 0.2 * np.sin(2 * np.pi * 0.3 * t[axis_moves] + k)
    return xyz


def test_nonwear_flags_still_block():
    fs = 10
    m = steps.nonwear_mask(wear_pattern(fs), fs, NONWEAR)
    h = 3600 * fs
    assert m[int(1.5 * h):int(3.5 * h)].all()
    assert not m[:int(1.5 * h)].any() and not m[int(3.5 * h):].any()


def test_nonwear_needs_two_still_axes():
    fs = 10
    assert steps.nonwear_mask(wear_pattern(fs, still_axes=3), fs, NONWEAR).any()
    # 2 แกนขยับตลอด -> นิ่งแค่แกนเดียว ไม่ถือว่าถอด
    assert not steps.nonwear_mask(wear_pattern(fs, still_axes=1), fs, NONWEAR).any()


def test_nonwear_needs_full_hour():
    fs = 10
    xyz = wear_pattern(fs)
    xyz[int(2.0 * 3600 * fs):int(2.1 * 3600 * fs), :2] += 0.3   # ขยับ 2 แกนสั้น ๆ กลางช่วงนิ่ง
    m = steps.nonwear_mask(xyz, fs, NONWEAR)
    assert not m[int(1.6 * 3600 * fs):int(3.4 * 3600 * fs)].all()


def test_nonwear_not_fooled_by_noise_range():
    # วางนิ่ง noise 6 mg ที่ 50 Hz: range ค่าดิบใน 1 ชม. ~55 mg (เกิน 50) แต่ยังต้องนับเป็นถอด
    fs = 50
    rng = np.random.default_rng(3)
    xyz = np.zeros((2 * 3600 * fs, 3), np.float32)
    xyz[:, 2] = 1
    xyz += rng.normal(0, 0.006, xyz.shape).astype(np.float32)
    assert np.ptp(xyz[:3600 * fs, 0]) > 0.05
    assert steps.nonwear_mask(xyz, fs, NONWEAR).all()

"""ฟังก์ชันอ่านผล clean สำหรับ notebook ตรวจผล (อ่าน parquet ทีละวัน ประหยัด RAM)"""
import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.clean.steps import BIT, FLAGS

FS = 50


def _flag_bits(reason):
    """คอลัมน์ reason (dictionary) -> bitmask uint16 ต่อแถว"""
    arr = reason.combine_chunks() if hasattr(reason, "combine_chunks") else reason
    lut = np.zeros(len(arr.dictionary) + 1, np.uint16)
    for i, name in enumerate(arr.dictionary.to_pylist()):
        for f in (name or "").split("|"):
            if f:
                lut[i] |= BIT[f]
    idx = pc.fill_null(arr.indices, len(arr.dictionary)).to_numpy()
    return lut[idx]


def epochs(path, epoch_s=10):
    """สรุปต่อ epoch: เวลา, วัน, ENMO เฉลี่ย (g) และสัดส่วนของแต่ละธง"""
    pf = pq.ParquetFile(path)
    out = []
    for i in range(pf.num_row_groups):
        t = pf.read_row_group(i, columns=["time_s", "x", "y", "z", "valid", "reason", "day"])
        ts = t["time_s"].to_numpy()
        ep = (ts // epoch_s).astype(np.int64)
        e0 = ep[0]
        k = ep - e0
        n = np.bincount(k)
        xyz = np.c_[t["x"].to_numpy(), t["y"].to_numpy(), t["z"].to_numpy()]
        enmo = np.maximum(np.linalg.norm(xyz, axis=1) - 1, 0)
        ok = ~np.isnan(enmo)
        rec = np.bincount(k, ok)
        row = {"epoch": np.arange(len(n)) + e0, "n": n,
               "enmo": np.bincount(k, np.where(ok, enmo, 0)) / np.maximum(rec, 1),
               "valid": np.bincount(k, t["valid"].to_numpy(zero_copy_only=False)) / n}
        bits = _flag_bits(t["reason"])
        for f in FLAGS:
            row[f] = np.bincount(k, (bits & BIT[f]) > 0) / n
        df = pd.DataFrame(row)
        df["day"] = int(t["day"][0].as_py())
        out.append(df[df["n"] > 0])
    df = pd.concat(out, ignore_index=True)
    df["time_s"] = df["epoch"] * epoch_s
    df["enmo"] = df["enmo"].where(df["missing"] < 1)
    return df


def window_features(path, win_s=10):
    """feature ต่อหน้าต่าง win_s วินาทีที่ valid ครบทั้งหน้าต่าง (ใช้ตรวจร่องรอยของเครื่อง)"""
    pf = pq.ParquetFile(path)
    win = int(win_s * FS)
    out = []
    freqs = np.fft.rfftfreq(win, 1 / FS)
    for i in range(pf.num_row_groups):
        t = pf.read_row_group(i, columns=["time_s", "x", "y", "z", "valid"])
        valid = t["valid"].to_numpy(zero_copy_only=False)
        if not valid.any():
            continue
        g = np.round(t["time_s"].to_numpy() * FS).astype(np.int64)
        w = g // win
        cnt = np.bincount(w - w[0], valid)
        full = np.flatnonzero(cnt == win) + w[0]
        sel = np.isin(w, full) & valid
        if not sel.any():
            continue
        X = np.c_[t["x"].to_numpy()[sel], t["y"].to_numpy()[sel], t["z"].to_numpy()[sel]].astype(np.float64)
        X = X.reshape(-1, win, 3)
        d = X - X.mean(1, keepdims=True)
        sd = d.std(1, ddof=1)
        mag = np.linalg.norm(X, axis=2)
        p = np.abs(np.fft.rfft(d, axis=1)) ** 2
        hf = p[:, freqs > 10].sum((1, 2)) / np.maximum(p[:, 1:].sum((1, 2)), 1e-20)
        q = X / 0.0039
        grid = (np.abs(q - np.round(q)) < 0.05).mean((1, 2))
        ac1 = (d[:, 1:] * d[:, :-1]).sum(1) / np.maximum((d ** 2).sum(1), 1e-20)
        uniq = np.mean([[len(np.unique(X[j, :, k])) / win for k in range(3)] for j in range(len(X))], axis=1)
        out.append(pd.DataFrame({
            "time_s": full * win_s,
            "sd_mg": sd.mean(1) * 1000,
            "sd_min_axis_mg": sd.min(1) * 1000,
            "mag_mean_g": mag.mean(1),
            "mag_sd_mg": mag.std(1, ddof=1) * 1000,
            "absdiff_mg": np.abs(np.diff(X, axis=1)).mean((1, 2)) * 1000,
            "hf_ratio": hf,
            "lag1_autocorr": ac1.mean(1),
            "on_3p9mg_grid": grid,
            "unique_frac": uniq,
        }))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

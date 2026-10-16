"""คำนวณเป้า noise ต่อแกนของ NHANES จาก COPS กลุ่ม train แล้วเทียบกับ target_mg ใน config

    python tools/noise_target.py               # เทียบ (คนที่ qc.json ยังไม่มีค่าต่อแกน วัดจาก parquet)
    python tools/noise_target.py --write-qc    # วัดจาก parquet แล้วเขียนค่าต่อแกนลง qc.json ที่ยังไม่มี
    python tools/noise_target.py --qc-only     # ใช้แค่ค่าใน qc.json (เร็ว ไม่อ่าน parquet)
    python tools/noise_target.py --tol 0.05    # เกณฑ์ความต่างที่ยอมได้ (ค่าเริ่มต้น 0.1 mg)

รันทุกครั้งหลัง clean COPS ใหม่ (เช่นแก้ calibration / quantize / nonwear)
exit code 1 = ค่าใน config ไม่ตรงกับ COPS ปัจจุบัน หรือ COPS train ยังไม่ครบ
ใช้ .qc.json / parquet ในเครื่อง ถ้ายังไม่มี: python tools/s3_pull.py get --what clean --dataset cops
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.clean import noise_target, run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="เป้า noise ต่อแกนจาก COPS กลุ่ม train")
    ap.add_argument("--qc-only", action="store_true", help="ไม่วัดจาก parquet")
    ap.add_argument("--write-qc", action="store_true", help="เขียนค่าที่วัดจาก parquet ลง qc.json")
    ap.add_argument("--tol", type=float, default=0.1, help="mg (ค่าเริ่มต้น 0.1)")
    ap.add_argument("--config", default=ROOT / "config" / "config.yaml")
    a = ap.parse_args()

    config = run.load_config(a.config)
    df = noise_target.cops_train_axes(config, run.load_subjects(config), ROOT,
                                      use_parquet=not a.qc_only, write_qc=a.write_qc)
    print(df.round(3).to_string(index=False))
    computed, configured, warn = noise_target.compare(config, df, a.tol)
    print(f"\nCOPS train {int((df.src != 'missing').sum())}/{len(df)} คน  "
          f"(จาก qc {int((df.src == 'qc').sum())}, จาก parquet {int((df.src == 'parquet').sum())})")
    print(f"เป้าที่คำนวณได้ (x, y, z): {computed.round(2).tolist()} mg")
    print(f"target_mg ใน config:      {'auto' if configured is None else configured.round(2).tolist()}")
    print(f"ใส่ใน config:  target_mg: [{', '.join(f'{v:.2f}' for v in computed)}]")
    for w in warn:
        print("คำเตือน:", w)
    sys.exit(1 if warn else 0)


if __name__ == "__main__":
    main()

"""Read-only S3 inventory/QC evidence for reference fit; never assigns a version."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import storage
from src.reference.common import load_roster, sha256, write_json
from src.reference.daily import METRICS


def render_report(out):
    """Render saved observed evidence without contacting S3 again."""
    out=Path(out)
    status=json.loads((out/'pilot_status.json').read_text(encoding='utf-8'))
    inventory=json.loads((out/'inventory.json').read_text(encoding='utf-8'))
    f=pd.read_csv(out/'source_coverage.csv')
    lines=['# Real pilot readiness audit', '',
        '**ยังไม่ได้ fit baseline จากข้อมูลจริง** — blocked: historical clean-run provenance.', '',
        'นี่คือผลตรวจไฟล์จริง/QC ไม่ใช่ผลทดลองโมเดล ไม่มี real baseline.json, daily.csv หรือ Analysis JSON ในรอบนี้', '',
        '| Dataset | Split | QC-included people with matching split/parquet | QC-reported days | QC-reported hours |',
        '| --- | --- | ---: | ---: | ---: |']
    for row in status['source_qc_summary']:
        lines.append(f"| {row['dataset']} | {row['split']} | {row['qc_included_people']} | {row['qc_reported_valid_days']} | {row['qc_reported_valid_hours']:.2f} |")
    lines += ['', 'วัน/ชั่วโมงข้างต้นมาจาก QC ยังไม่ใช่รายวัน/coverage ของ ENMO, cadence หรือ tremor',
        'จำนวน usable days/people ต่อตัวชี้วัดยังคำนวณไม่ได้: ดู coverage_report.json (null/blocked)', '',
        f"พบ QC {status['n_clean_qc']} ไฟล์ และ hourly CSV {status['n_hourly_csv']} ไฟล์", '',
        '## หลักฐานที่ตรวจแล้ว', '',
        '- inventory.json เก็บ S3 object identity/เวลาตรวจ; qc/ เก็บ QC จริงและ source_coverage.csv เก็บ SHA256',
        '- ไม่พบ clean/feature manifest หรือ historical config/code records ใน prefixes ที่ค้นด้านล่าง',
        '- object user metadata ตัวอย่างมีเพียง MD5 ไม่บอก clean version/config',
        '- reports/signal_audit/manifest.json เป็นหลักฐาน identity และเวอร์ชัน audit ไม่ใช่เวอร์ชัน clean',
        '- current config หรือ snapshot ตอน audit ไม่พิสูจน์ original clean settings', '',
        '## ขอบเขตค้น', '']
    lines += [f"- {r['prefix']} ({r['n_files']} objects)" for r in inventory['searched']]
    lines += ['', 'ไม่สรุปว่าไม่มีหลักฐานใน branch, run log หรือระบบอื่นที่ยังเข้าไม่ถึง', '',
        '## หลักฐานที่ขอจากทีม', '',
        '1. Original clean run log/manifest ที่ผูก parquet/QC ของแต่ละคนกับ output hashes/object versions',
        '2. Config ที่ใช้จริงใน run นั้น รวม clean thresholds/noise settings และ code revision/source archive',
        '3. ถ้าใช้ hourly เดิม: extractor source/config/units และ linkage ไป clean run เดียวกัน มิฉะนั้น regenerate จาก clean ที่ยืนยันแล้ว', '',
        'รายชื่อไฟล์ที่ยังไม่มีหลักฐานตรวจได้อยู่ใน source_coverage.csv คอลัมน์ clean_manifest_uri',
        'ตัวอย่าง 16 pilot candidates ที่ยัง NOT APPROVED (8 train ต่อกลุ่ม เลือกเรียง ID ก่อนดู feature):', '']
    pilot=pd.read_csv(out/'pilot_candidates_NOT_APPROVED.csv')
    lines += [f"- {row.clean_manifest_uri}" for row in pilot.itertuples()]
    lines += ['', 'รูปแบบรับหลักฐานและคำสั่ง extract/daily/fit/compare/full-train/held-out อยู่ใน README_reference_baseline.md',
        'ต้องนำหลักฐานเดิมมาจัดรูปแบบ ห้ามสร้าง run/version/config ย้อนหลังจากการเดา', '',
        '## ส่วนที่ทำต่อได้และข้อจำกัด', '',
        '- เพิ่ม gate ก่อน extraction/daily/real fit ป้องกัน verified=True ที่ไม่มี historical clean evidence',
        '- เตรียม AWS prebuilt/script-mode config/source archive แบบไม่ upload/submit',
        '- ใช้ Python ปกติ fit ได้ Docker daemon ไม่ใช่ตัวขวางข้อมูลรอบนี้',
        '- ไม่มีการแก้ split, clean/QC thresholds หรือดึง valid/test เข้า fit',
        '- NHANES valid/test และ COPS test ยังไม่พบ clean/QC ใน prefixes ที่ตรวจ',
        '- Pilot ที่จะได้ในอนาคตเป็นความห่างจาก COPS/NHANES แยกกลุ่ม ไม่ใช่ clinical severity/diagnostic probability', '']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise ValueError('Use a new audit output directory')
    pipeline = yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    storage.configure(pipeline['storage'])
    uri = storage.s3_root(pipeline).rstrip('/')
    root = uri.split('://', 1)[1]
    fs = storage.fs_of(uri)
    roster = load_roster(pipeline=pipeline)
    inventory, searched = {}, []
    # Search likely evidence locations, excluding large raw/processed sensor trees.
    for relative in ['data/clean', 'data/features', 'metadata', 'config', 'reports', 'logs', 'manifests']:
        prefix = root+'/'+relative
        try:
            items = fs.find(prefix, detail=True)
        except FileNotFoundError:
            items = {}
        searched.append(dict(prefix='s3://'+prefix, n_files=len(items)))
        for key, info in items.items():
            if info.get('type') == 'file':
                inventory[key] = dict(size=info.get('size'), etag=info.get('ETag'),
                                      modified=str(info.get('LastModified', '')))
    a.out.mkdir(parents=True)
    qkeys = sorted(k for k in inventory if '/data/clean/' in k and k.endswith('.qc.json'))

    def read_qc(key):
        raw = fs.cat(key)
        q = json.loads(raw)
        local = a.out/'qc'/q['dataset']/Path(key).name
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)
        return key, q, hashlib.sha256(raw).hexdigest()

    with ThreadPoolExecutor(max_workers=8) as pool:
        qcs = list(pool.map(read_qc, qkeys))
    rows = []
    for key, q, digest in qcs:
        match = roster[roster.dataset.eq(q['dataset']) & roster.subject_id.eq(q['subject_id'])]
        split = match.iloc[0].split if len(match) == 1 else None
        parquet = key[:-len('.qc.json')]+'.parquet'
        sidecar = key[:-len('.qc.json')]+'.clean.manifest.json'
        included = q.get('included') is True and q.get('status') == 'ok'
        identity = split is not None and q.get('split') == split
        rows.append(dict(dataset=q['dataset'], subject_id=q['subject_id'], split=split,
            qc_split=q.get('split'), identity_matches_roster=identity, qc_included=included,
            parquet_present=parquet in inventory, qc_valid_days=q.get('valid_days'),
            qc_valid_hours=q.get('hours_valid'), fs_native=q.get('fs_native'), fs_out=q.get('fs_out'),
            qc_uri='s3://'+key, qc_sha256=digest, parquet_uri='s3://'+parquet,
            clean_manifest_uri='s3://'+sidecar, clean_manifest_present=sidecar in inventory,
            provenance_status='requires_manifest_review' if sidecar in inventory else 'missing_clean_run_evidence',
            preprocessing_version=None))
    f = pd.DataFrame(rows)
    f.to_csv(a.out/'source_coverage.csv', index=False)
    summaries = []
    for (ds, split), group in f.groupby(['dataset','split']):
        good = group[group.qc_included & group.identity_matches_roster & group.parquet_present]
        summaries.append(dict(dataset=ds, split=split, n_qc=len(group),
            qc_included_people=len(good), qc_reported_valid_days=int(good.qc_valid_days.sum()),
            qc_reported_valid_hours=float(good.qc_valid_hours.sum()),
            provenance_verified_people=0))
    selected = f[f.split.eq('train') & f.qc_included & f.identity_matches_roster & f.parquet_present]
    pilot = selected.sort_values(['dataset','subject_id']).groupby('dataset', sort=True).head(8)
    pilot.to_csv(a.out/'pilot_candidates_NOT_APPROVED.csv', index=False)
    feature_keys = [k for k in inventory if '/data/features/' in k and k.endswith('.csv') and inventory[k]['size']]
    sidecars = [k for k in inventory if any(t in Path(k).name.lower() for t in ['manifest','provenance','config','version'])]
    metadata = {}
    for ds in ['cops','nhanes']:
        subset = pilot[pilot.dataset.eq(ds)]
        if len(subset):
            key = subset.iloc[0].parquet_uri.split('://', 1)[1]
            metadata['s3://'+key] = fs.metadata(key)
    metric_coverage = [dict(dataset=ds,metric=m,unit=definition['unit'],
        status='blocked_unverified_preprocessing',n_verified_people=0,n_verified_days=0,
        n_usable_people=None,n_usable_days=None,reason='not_computed_without_verified_clean_and_hourly_lineage')
        for ds in ['cops','nhanes'] for m,definition in METRICS.items()]
    write_json(a.out/'coverage_report.json',dict(data_kind='real',source_qc_summary=summaries,
        note='QC-reported totals are not daily indicator coverage and do not establish provenance.',
        indicators=metric_coverage))
    write_json(a.out/'inventory.json',dict(checked_utc=datetime.now(timezone.utc).isoformat(),
        searched=searched,files=inventory,evidence_candidates=sidecars,sample_parquet_user_metadata=metadata,
        local_evidence_candidates=[str(p.relative_to(ROOT)) for base in ['metadata','config','reports/signal_audit']
            for p in (ROOT/base).rglob('*manifest*.json')],
        current_config_sha256=sha256(ROOT/'config/config.yaml'),
        audit_code_sha256=sha256(__file__),
        split_source_sha256={name:sha256(ROOT/pipeline['clean']['subjects'][name])
            for name in ['cops_splits','nhanes_selected']},
        current_config_is_not_historical_clean_proof=True))
    status=dict(status='blocked_clean_provenance_requires_review',data_kind='real',real_baseline_fitted=False,
        baseline_artifact=None,daily_features=None,analysis_json=None,
        n_clean_qc=len(qcs),n_hourly_csv=len(feature_keys),source_qc_summary=summaries,
        missing_evidence=[
            'Clean-run record linking each parquet and QC to their hashes/object versions, recorded resolved clean config and clean source version.',
            'Historical clean source files/commit/archive and original config must come from actual run records; current checkout snapshots cannot substitute.',
            'Hourly lineage linking CSV hash, extractor code/config/units and source clean record; otherwise re-extract only after clean evidence is verified.'],
        exact_expected_clean_records=pilot.clean_manifest_uri.tolist(),
        evidence_candidates=sidecars,thresholds_and_splits_changed=False,
        interpretation='No reference fit attempted. Zero verified days means unverified, not zero observed usable signal.')
    write_json(a.out/'pilot_status.json',status)
    render_report(a.out)
    print(json.dumps(dict(status=status['status'],n_clean_qc=len(qcs),n_hourly_csv=len(feature_keys),
                         summary=summaries,evidence_candidates=sidecars),indent=2))


if __name__ == '__main__':
    main()

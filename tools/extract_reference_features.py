"""Reuse Mew_Feature extractor on existing clean, with strict QC and provenance.

No ingestion, no cleaning, no upload. Writes a NEW versioned feature directory.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.features import run as extractor
from src.reference.common import fingerprint, load_config, load_roster, sha256, write_json
from src.reference.provenance import validate_clean_record


def extract(dataset, sid, parquet, qc_path, out_root, roster, cfg, pipeline, clean_manifest=None):
    q = json.loads(Path(qc_path).read_text(encoding='utf-8'))
    row = roster[roster.dataset.eq(dataset) & roster.subject_id.eq(sid)]
    if len(row) != 1:
        raise ValueError('Unknown subject; use authoritative splits')
    split = row.iloc[0].split
    evidence_path = Path(clean_manifest) if clean_manifest else Path(parquet).with_suffix('.clean.manifest.json')
    if not evidence_path.exists():
        raise ValueError(f'Unverified historical clean provenance: missing {evidence_path}; '
                         'supply original run config/code/output evidence, never backfill it from current checkout')
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    validate_clean_record(evidence, pipeline['clean'], dataset, sid, split)
    if evidence['parquet_sha256'] != sha256(parquet) or evidence['qc_sha256'] != sha256(qc_path):
        raise ValueError('Clean parquet/QC hashes do not match historical run evidence')
    for k, v in dict(subject_id=sid, dataset=dataset, split=split,
                     fs_native=pipeline['datasets'][dataset]['sampling_rate_hz'], fs_out=50).items():
        if q.get(k) != v:
            raise ValueError(f'QC mismatch: {sid} {k}')
    if q.get('status') != 'ok' or q.get('included') is not True:
        raise ValueError(f'insufficient_data: {sid} not included by existing clean QC')
    if pipeline['clean']['resample']['fs_out'] != extractor.FS:
        raise ValueError('Extractor sampling rate differs from clean configuration')
    target = pipeline['clean']['harmonize']['add_noise']
    if dataset in target['datasets']:
        if q.get('noise_target_axes_mg') is None or (target['target_mg'] != 'auto' and not np.allclose(q['noise_target_axes_mg'], target['target_mg'])):
            raise ValueError('QC noise target is stale; do not silently reclean')
    if list(extractor.CFG['day_hours']) != cfg['feature_policy']['clock_hours'] or extractor.WIN_S != cfg['feature_policy']['window_seconds']:
        raise ValueError('Extractor/reporting config mismatch')
    out = Path(out_root) / dataset / f'{sid}.csv'
    if out.exists() or out.with_suffix('.manifest.json').exists():
        raise ValueError('Versioned output exists; use a new output root, never silently overwrite')
    days = [d['day'] for d in q['days'] if d.get('valid')]
    if (len(days) != q.get('valid_days') or len(days) < pipeline['clean']['days']['min_valid_days']
            or len(days) != len(set(days))
            or any(d['valid_h'] < pipeline['clean']['days']['min_valid_hours'] for d in q['days'] if d.get('valid'))):
        raise ValueError('QC included/day consistency mismatch; do not silently override clean criteria')
    parts = []
    for day in days:
        t = pq.read_table(parquet, columns=['time_s','x','y','z'], filters=[('day','=',day),('valid','=',True)])
        xyz = np.column_stack([t[k].to_numpy() for k in ['x','y','z']])
        for starts, windows in extractor.make_windows(t['time_s'].to_numpy(), xyz):
            keep = ((starts // 3600) % 24 >= extractor.CFG['day_hours'][0]) & ((starts // 3600) % 24 < extractor.CFG['day_hours'][1])
            starts, windows = starts[keep], windows[keep]
            if not len(starts):
                continue
            f = pd.DataFrame(extractor.window_features(windows))
            f['day'], f['hour_idx'] = day, (starts // 3600).astype(int)
            f['hour'] = f.hour_idx % 24
            parts.append(f)
    columns = ['subject_id','dataset','split','day','hour','hour_idx','n_windows','valid_min','n_walk','n_clean_walk','n_rest','n_tremor',
               'enmo_median','step_freq_walk_median','tremor_frac_of_rest']
    hourly = extractor.aggregate_hours(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame()
    if len(hourly):
        hourly.insert(0, 'split', split)
        hourly.insert(0, 'dataset', dataset)
        hourly.insert(0, 'subject_id', sid)
    else:
        hourly = pd.DataFrame(columns=columns)
    out.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_csv(out, index=False)
    version = fingerprint(dict(extractor_sha256=sha256(extractor.__file__), cfg=extractor.CFG,
                               fs=extractor.FS, window_s=extractor.WIN_S, clean_config=evidence['resolved_clean_config'],
                               clean_code_version=evidence['code_version']))
    manifest = dict(verified=True, data_kind='real', dataset=dataset, subject_id=sid, split=split,
                    feature_contract=cfg['feature_contract'], preprocessing_version=version,
                    source_branch='origin/Mew_Feature', source_commit='37d5722',
                    extractor_sha256=sha256(extractor.__file__), extractor_config=extractor.CFG,
                    hourly_sha256=sha256(out), clean_parquet_sha256=sha256(parquet), qc_sha256=sha256(qc_path),
                    qc_valid_days=days, source_clean_config_snapshot=pipeline['clean'],
                    clean_run_evidence=evidence, clean_run_evidence_sha256=fingerprint(evidence),
                    clean_config_verification='original_run_evidence_validated_against_supported_source_and_config',
                    units=dict(enmo_median='g', step_freq_walk_median='steps/s', tremor_frac_of_rest='fraction'),
                    status='ok' if len(hourly) else 'insufficient_data', reason='' if len(hourly) else 'no_complete_reportable_windows')
    write_json(out.with_suffix('.manifest.json'), manifest)
    return hourly


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', choices=['cops','nhanes'], required=True)
    p.add_argument('--ids', nargs='+', required=True)
    p.add_argument('--clean-root', type=Path, default=ROOT/'data/clean')
    p.add_argument('--qc-root', type=Path)
    p.add_argument('--clean-manifest-root', type=Path, help='Original clean run evidence: <root>/<dataset>/<id>.clean.manifest.json')
    p.add_argument('--out', type=Path)
    p.add_argument('--config', type=Path, default=ROOT/'config/reference_baseline.yaml')
    args = p.parse_args()
    cfg = load_config(args.config)
    pipeline = yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    roster = load_roster(pipeline=pipeline)
    for sid in args.ids:
        parquet = args.clean_root / args.dataset / f'{sid}.parquet'
        if not parquet.exists():
            parquet = args.clean_root / f'{sid}.parquet'  # supports existing audit cache without copying sensors
        qc = (args.qc_root or args.clean_root) / args.dataset / f'{sid}.qc.json'
        evidence = args.clean_manifest_root / args.dataset / f'{sid}.clean.manifest.json' if args.clean_manifest_root else None
        f = extract(args.dataset, sid, parquet, qc, args.out or ROOT/cfg['paths']['hourly'], roster, cfg, pipeline, evidence)
        print(f'{sid}: {len(f)} versioned hourly rows')


if __name__ == '__main__':
    main()

"""Versioned feature provenance and local/SageMaker input bundles."""
import json
from pathlib import Path

import pandas as pd
import yaml

from .common import fingerprint, sha256, write_json
from .daily import aggregate_daily
from .provenance import validate_feature_record


def create_bundle(hourly_root, out, roster, cfg, pipeline_cfg, split_sources):
    root, out = Path(hourly_root), Path(out)
    if out.exists():
        raise ValueError('Output bundle already exists; use a new run directory')
    frames, manifests, expected_days = [], [], []
    for csv in sorted(root.rglob('*.csv')):
        sidecar = csv.with_suffix('.manifest.json')
        if not sidecar.exists():
            raise ValueError(f'Missing feature provenance: {csv}; legacy files must be regenerated')
        m = json.loads(sidecar.read_text(encoding='utf-8'))
        if not m.get('verified') or m.get('feature_contract') != cfg['feature_contract'] or m.get('hourly_sha256') != sha256(csv):
            raise ValueError(f'Feature hash/contract/provenance mismatch: {csv}')
        if m.get('data_kind') != 'real' or not m.get('preprocessing_version'):
            raise ValueError('Real bundle requires versioned real extraction manifests')
        validate_feature_record(m, cfg)
        f = pd.read_csv(csv)
        for k in ['dataset', 'subject_id', 'split']:
            if not f[k].eq(m[k]).all():
                raise ValueError('Feature identity does not match manifest')
        if not set(f.day) <= set(m['qc_valid_days']):
            raise ValueError('Feature day is not included by source QC')
        frames.append(f)
        manifests.append(m)
        expected_days.extend(dict(dataset=m['dataset'], subject_id=m['subject_id'], split=m['split'], day=d) for d in m['qc_valid_days'])
    if not frames:
        raise ValueError('No versioned hourly features; run extraction first')
    versions = {m['preprocessing_version'] for m in manifests}
    if len(versions) != 1:
        raise ValueError('Cannot mix feature/clean preprocessing signatures')
    provenance = dict(verified=True, data_kind='real', preprocessing_version=versions.pop(),
                      feature_contract=cfg['feature_contract'], config_hash=fingerprint(cfg),
                      split_sources=split_sources, sources=manifests,
                      clean_provenance_status='original_run_evidence_validated')
    hourly = pd.concat(frames, ignore_index=True)
    daily, qc = aggregate_daily(hourly, roster, cfg, provenance, expected_days=expected_days)
    write_bundle(out, daily, qc, roster, cfg, pipeline_cfg, provenance)
    return daily, qc


def write_bundle(out, daily, hour_qc, roster, cfg, pipeline_cfg, provenance):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    demo_tags={k:provenance[k] for k in ['is_demo','data_origin','baseline_version','schema_version']} if provenance.get('is_demo') else {}
    for name,frame in [('daily',daily),('hour_qc',hour_qc),('roster',roster)]:
        frame=frame.assign(**demo_tags)
        frame.to_csv(out / f'{name}.csv', index=False)
    (out / 'reference_config.yaml').write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding='utf-8')
    (out / 'pipeline_config.yaml').write_text(yaml.safe_dump(pipeline_cfg, allow_unicode=True), encoding='utf-8')
    coverage=[]
    for (dataset,split,metric), group in daily.groupby(['dataset','split','metric']):
        usable=group[group.status.eq('ok')]
        counts=usable.groupby('subject_id').size()
        eligible=counts[counts >= cfg['reference_policy']['min_days_per_participant']]
        coverage.append(dict(dataset=dataset,split=split,metric=metric,
            n_people_observed=int(group.subject_id.nunique()),n_days_observed=len(group),
            n_people_daily_usable=int(usable.subject_id.nunique()),n_days_daily_usable=len(usable),
            n_people_meeting_reference_day_minimum=len(eligible),
            n_days_meeting_reference_day_minimum=int(eligible.sum()),
            insufficient_reasons=group.loc[group.status.ne('ok'),'reasons'].value_counts().to_dict()))
    write_json(out/'coverage_report.json',dict(**demo_tags,data_kind=provenance['data_kind'],
        preprocessing_version=provenance['preprocessing_version'],by_cohort_split_metric=coverage))
    manifest_tags={k:v for k,v in demo_tags.items() if k!='schema_version'}
    if demo_tags:
        manifest_tags['output_schema_version']=demo_tags['schema_version']
    write_json(out / 'bundle.manifest.json', dict(**manifest_tags,schema_version=1, provenance=provenance,
        files={name:sha256(out / name) for name in ['daily.csv','hour_qc.csv','roster.csv','reference_config.yaml','pipeline_config.yaml','coverage_report.json']}))


def load_bundle(out):
    out = Path(out)
    m = json.loads((out / 'bundle.manifest.json').read_text(encoding='utf-8'))
    required = {'daily.csv','hour_qc.csv','roster.csv','reference_config.yaml','pipeline_config.yaml'}
    if m.get('schema_version') != 1 or not required <= set(m.get('files', {})):
        raise ValueError('Invalid bundle manifest')
    for name, expected in m['files'].items():
        if Path(name).name != name or sha256(out / name) != expected:
            raise ValueError('Bundle file hash mismatch or unsafe manifest path')
    cfg = yaml.safe_load((out / 'reference_config.yaml').read_text(encoding='utf-8'))
    return pd.read_csv(out / 'daily.csv'), pd.read_csv(out / 'roster.csv'), cfg, m['provenance']

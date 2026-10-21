"""Daily aggregation / train-only fit / cohort-reference comparisons. No AWS jobs."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.reference.common import load_config, load_roster, sha256, write_json
from src.reference.io import create_bundle, load_bundle
from src.reference.model import compare_daily, fit_reference, load_reference, save_reference


def fit_bundle(bundle, out):
    daily, roster, cfg, provenance = load_bundle(bundle)
    artifact = fit_reference(daily, roster, cfg, provenance)
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output model directory exists; choose a new run')
    out.mkdir(parents=True, exist_ok=True)
    save_reference(out/'baseline.json', artifact)
    write_json(out/'fit_report.json', dict(data_kind=artifact['data_kind'], artifact_id=artifact['artifact_id'],
        is_demo=artifact['is_demo'],data_origin=artifact['data_origin'],baseline_version=artifact['baseline_version'],schema_version='1.0.0',
        fit_split='train', references={ds:{metric:v['summary'] for metric,v in metrics.items()} for ds,metrics in artifact['references'].items()},
        warning='Cohort distances only. No diagnostic probability, severity score, classifier or combined indicator.'))
    return artifact


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    d = sub.add_parser('daily')
    d.add_argument('--hourly-root', type=Path, required=True)
    d.add_argument('--out', type=Path, required=True)
    d.add_argument('--config', type=Path, default=ROOT/'config/reference_baseline.yaml')
    f = sub.add_parser('fit')
    f.add_argument('--bundle', type=Path, required=True)
    f.add_argument('--out', type=Path, required=True)
    s = sub.add_parser('compare')
    s.add_argument('--bundle', type=Path, required=True)
    s.add_argument('--baseline', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.action == 'daily':
        cfg = load_config(a.config)
        pipeline = yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
        sources = {v:sha256(ROOT/v) for v in pipeline['clean']['subjects'].values()}
        daily, _ = create_bundle(a.hourly_root, a.out, load_roster(pipeline=pipeline), cfg, pipeline, sources)
        print(f'Daily bundle: {len(daily)} metric-day rows; {daily.status.value_counts().to_dict()}')
    elif a.action == 'fit':
        artifact = fit_bundle(a.bundle, a.out)
        print(f"Fit {artifact['data_kind']} reference: {artifact['artifact_id']}")
    else:
        daily, _, _, _ = load_bundle(a.bundle)
        artifact = load_reference(a.baseline)
        from src.reference.output import build_analysis
        write_json(a.out, build_analysis(daily, artifact))


if __name__ == '__main__':
    main()

"""Track B inputs (exploratory): COPS train/valid symptom diaries, legacy clean QC and legacy hourly features from S3.

  python -X utf8 -m backtest.prepare_symptom [--ids COPS-8 COPS-10]
  python -X utf8 -m backtest.run --mode real --tracks symptom --symptom-snapshot <printed dir> --run-id <new>

Only train/valid COPS IDs from the authoritative roster are requested; no test key is ever listed or read
(tools/audit_model_readiness.py fetches every split, so it is not used). Labels come from its candidate_labels().
Legacy feature CSVs have no provenance manifest: acceptable for this exploratory track only, never for the
reference baseline. Writes a new snapshot backtest/workspace/real/symptom/<UTC>/ and never overwrites one.
"""
import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.reference.common import ROOT, load_roster, write_json

from backtest.runners.data_loader import pipeline_config

KEYS = dict(diary='data/raw/cops/{sid}/{sid}_symptomdiary.csv', qc='data/clean/cops/{sid}.qc.json',
            features='data/features/cops/{sid}.csv')
LABEL_COLUMNS = ['subject_id', 'split', 'day', 'hour', 'tremor_score', 'kinesia_score', 'duplicate_hour',
                 'candidate', 'candidate_tremor']


def select_subjects(roster, ids=None):
    """COPS train/valid only; asking for any other ID is an error, not a silent skip."""
    allowed = roster[roster.dataset.eq('cops') & roster.split.isin(['train', 'valid'])]
    if ids:
        unknown = sorted(set(ids) - set(allowed.subject_id))
        if unknown:
            raise ValueError(f'Not COPS train/valid subjects (test is never read): {unknown}')
        allowed = allowed[allowed.subject_id.isin(ids)]
    return allowed[['subject_id', 'split']].to_dict('records')


def build_snapshot(out, subjects, fetch):
    """fetch(relative S3 key) -> bytes or None. Writes features/, labels.csv, all_diary_hours.csv, manifest.json."""
    from tools.audit_model_readiness import candidate_labels
    out = Path(out)
    if out.exists():
        raise ValueError(f'Snapshot exists: {out}; never overwrite')
    (out / 'features').mkdir(parents=True)
    entries, frames = [], []
    for s in subjects:
        sid = s['subject_id']
        got = {k: fetch(t.format(sid=sid)) for k, t in KEYS.items()}
        entry = dict(subject_id=sid, split=s['split'],
                     sources={k: dict(key=KEYS[k].format(sid=sid), sha256=hashlib.sha256(v).hexdigest() if v is not None else None)
                              for k, v in got.items()})
        missing = [k for k, v in got.items() if v is None]
        if missing:
            entries.append(dict(entry, status='missing', missing=missing))
            continue
        (out / 'features' / f'{sid}.csv').write_bytes(got['features'])
        diary = pd.read_csv(io.BytesIO(got['diary']), sep=';', dtype=str, keep_default_na=False)
        d = candidate_labels(diary, json.loads(got['qc']))
        d['subject_id'], d['split'] = sid, s['split']
        frames.append(d[LABEL_COLUMNS])
        entries.append(dict(entry, status='ok', n_diary_hours=len(d), n_candidate_tremor=int(d.candidate_tremor.sum())))
    hours = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=LABEL_COLUMNS)
    hours.to_csv(out / 'all_diary_hours.csv', index=False)  # review only, includes non-candidate hours
    labels = hours[hours.candidate_tremor.astype(bool)].copy()
    labels[['day', 'hour']] = labels[['day', 'hour']].astype(int)  # candidate rows always have both
    labels.to_csv(out / 'labels.csv', index=False)
    counts = labels.groupby(['split', 'tremor_score']).agg(hours=('subject_id', 'size'), subjects=('subject_id', 'nunique'))
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), kind='symptom_track_b_inputs', data_kind='real',
                    splits_requested=['train', 'valid'], test_keys_requested=0,
                    note='Legacy features without provenance manifests: exploratory Track B only, never the reference baseline.',
                    labels='tools/audit_model_readiness.candidate_labels (diary Day N / Time H -> day N, hour H-1)',
                    subjects=entries, label_counts=counts.reset_index().to_dict('records'))
    write_json(out / 'manifest.json', manifest)
    return manifest


def s3_fetch():
    from src import storage
    cfg = pipeline_config()
    storage.configure(cfg['storage'])
    root = storage.s3_root(cfg).rstrip('/')
    fs, prefix = storage.fs_of(root), root.split('://', 1)[1]

    def fetch(rel):
        try:
            return fs.cat(f'{prefix}/{rel}')
        except FileNotFoundError:
            return None
    return fetch


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ids', nargs='+', help='subset of COPS train/valid subject IDs')
    p.add_argument('--out', type=Path, help='new snapshot directory (default backtest/workspace/real/symptom/<UTC>)')
    a = p.parse_args(argv)
    out = a.out or ROOT / 'backtest/workspace/real/symptom' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    manifest = build_snapshot(out, select_subjects(load_roster(), a.ids), s3_fetch())
    ok = [e for e in manifest['subjects'] if e['status'] == 'ok']
    print(f"{len(ok)}/{len(manifest['subjects'])} subjects with diary+QC+features -> {out}")
    for e in manifest['subjects']:
        if e['status'] != 'ok':
            print(f"  missing {e['subject_id']} ({e['split']}): {e['missing']}")
    print(pd.DataFrame(manifest['label_counts']).to_string(index=False))
    print(f'Next: python -X utf8 -m backtest.run --mode real --tracks symptom --symptom-snapshot {out} --run-id <new>')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

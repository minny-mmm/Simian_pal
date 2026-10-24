"""Fail closed on real data whose historical clean run cannot be established.

This module validates supplied run evidence. It NEVER generates historical evidence.
"""
import re
import ast

from .common import ROOT, fingerprint, sha256

CLEAN_SOURCE_PATHS = [f'src/{path}' for path in [
    'clean/__init__.py', 'clean/run.py', 'clean/steps.py', 'clean/noise_target.py', 'clean/report.py',
    'ingestion/__init__.py', 'ingestion/base.py', 'ingestion/cops_reader.py',
    'ingestion/nhanes_reader.py', 'ingestion/unzip_cops.py']]


def validate_clean_record(record, clean_config, dataset, subject_id, split):
    if record.get('schema_version') != 1 or record.get('kind') != 'clean_run_evidence':
        raise ValueError('Missing/unsupported historical clean run evidence')
    for key, expected in dict(dataset=dataset, subject_id=subject_id, split=split).items():
        if record.get(key) != expected:
            raise ValueError(f'Clean evidence identity mismatch: {key}')
    if not record.get('run_id') or not record.get('recorded_at_utc') or not record.get('evidence_uri'):
        raise ValueError('Clean evidence requires original run ID, timestamp and evidence URI')
    if record.get('resolved_clean_config') != clean_config:
        raise ValueError('Recorded historical clean config differs from supported config; review explicitly')
    code = record.get('code_version', {})
    if not re.fullmatch(r'[0-9a-f]{40}', str(code.get('git_commit', ''))):
        raise ValueError('Missing historical clean code version')
    # Check the recorded source bytes, rather than treating current HEAD as the clean version.
    for relative in CLEAN_SOURCE_PATHS:
        path = ROOT/relative
        if not path.is_file() or code.get('files', {}).get(relative) != sha256(path):
            raise ValueError(f'Missing/unsupported historical source hash: {relative}')
    for key in ['parquet_sha256', 'qc_sha256']:
        if not re.fullmatch(r'[0-9a-f]{64}', str(record.get(key, ''))):
            raise ValueError(f'Missing clean output identity: {key}')
    return record


def validate_feature_record(m, cfg):
    # Read constants without importing signal-processing dependencies into the fit runtime.
    extractor_path = ROOT/'src/features/run.py'
    constants = {}
    for node in ast.parse(extractor_path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ['CFG', 'FS', 'WIN_S']:
                constants[name] = ast.literal_eval(node.value)
    record = m.get('clean_run_evidence', {})
    if not record or m.get('clean_run_evidence_sha256') != fingerprint(record):
        raise ValueError('Unverified historical clean provenance: evidence missing or hash mismatch')
    validate_clean_record(record, m.get('source_clean_config_snapshot'), m.get('dataset'), m.get('subject_id'), m.get('split'))
    if m.get('clean_parquet_sha256') != record['parquet_sha256'] or m.get('qc_sha256') != record['qc_sha256']:
        raise ValueError('Feature sources differ from clean-run evidence')
    expected = fingerprint(dict(extractor_sha256=sha256(extractor_path), cfg=constants['CFG'],
        fs=constants['FS'], window_s=constants['WIN_S'], clean_config=record['resolved_clean_config'],
        clean_code_version=record['code_version']))
    if (m.get('verified') is not True or m.get('data_kind') != 'real'
            or m.get('extractor_sha256') != sha256(extractor_path)
            or m.get('feature_contract') != cfg['feature_contract']
            or m.get('preprocessing_version') != expected):
        raise ValueError('Unsupported/unverified extractor or preprocessing signature')


def validate_real_fit_provenance(provenance, train, cfg):
    if provenance.get('data_kind') != 'real':
        return
    sources = provenance.get('sources', [])
    if not sources:
        raise ValueError('Unverified historical clean provenance: no source run records')
    identities = set()
    for m in sources:
        validate_feature_record(m, cfg)
        if m['preprocessing_version'] != provenance.get('preprocessing_version'):
            raise ValueError('Mixed source preprocessing versions')
        identities.add((m['dataset'], m['subject_id'], m['split']))
    if not set(train[['dataset','subject_id','split']].itertuples(index=False, name=None)) <= identities:
        raise ValueError('Missing clean/feature lineage for a train participant')

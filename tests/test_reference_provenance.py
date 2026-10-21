"""SYNTHETIC evidence objects only; no claims about historical patient processing."""
from copy import deepcopy

import pytest
import yaml

from src.reference.common import ROOT, fingerprint, load_config, sha256
from src.reference.provenance import CLEAN_SOURCE_PATHS, validate_clean_record, validate_feature_record


def synthetic_record():
    cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))['clean']
    record=dict(schema_version=1,kind='clean_run_evidence',dataset='cops',subject_id='SYNTHETIC',split='train',
        run_id='SYNTHETIC-NOT-A-REAL-RUN',recorded_at_utc='2026-01-01T00:00:00Z',
        evidence_uri='SYNTHETIC-TEST-ONLY',resolved_clean_config=deepcopy(cfg),
        code_version=dict(git_commit='0'*40,files={p:sha256(ROOT/p) for p in CLEAN_SOURCE_PATHS}),
        parquet_sha256='0'*64,qc_sha256='1'*64)
    return cfg,record


def test_record_checks_source_bytes_config_and_identity():
    cfg,record=synthetic_record()
    assert validate_clean_record(record,cfg,'cops','SYNTHETIC','train')==record
    for field in ['run_id','recorded_at_utc','evidence_uri','code_version','resolved_clean_config','qc_sha256']:
        broken=deepcopy(record)
        broken.pop(field)
        with pytest.raises(ValueError):
            validate_clean_record(broken,cfg,'cops','SYNTHETIC','train')
    record['code_version']['files']['src/clean/steps.py']='f'*64
    with pytest.raises(ValueError,match='source hash'):
        validate_clean_record(record,cfg,'cops','SYNTHETIC','train')


def test_record_does_not_relabel_test_as_train_or_accept_different_config():
    cfg,record=synthetic_record()
    with pytest.raises(ValueError,match='identity'):
        validate_clean_record(record,cfg,'cops','SYNTHETIC','test')
    changed=deepcopy(cfg)
    changed['days']['min_valid_hours']=1
    with pytest.raises(ValueError,match='config'):
        validate_clean_record(record,changed,'cops','SYNTHETIC','train')


def test_feature_record_binds_clean_outputs_and_supported_extractor_without_importing_scipy():
    import ast
    cfg,record=synthetic_record()
    extractor=ROOT/'src/features/run.py'
    constants={}
    for node in ast.parse(extractor.read_text(encoding='utf-8')).body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ['CFG','FS','WIN_S']:
            constants[node.targets[0].id]=ast.literal_eval(node.value)
    contract=load_config()
    signature=fingerprint(dict(extractor_sha256=sha256(extractor),cfg=constants['CFG'],
        fs=constants['FS'],window_s=constants['WIN_S'],clean_config=cfg,clean_code_version=record['code_version']))
    # Mock metadata exercises the real-data guard only; it is never a patient artifact.
    m=dict(verified=True,data_kind='real',dataset='cops',subject_id='SYNTHETIC',split='train',
        clean_run_evidence=record,clean_run_evidence_sha256=fingerprint(record),
        source_clean_config_snapshot=cfg,clean_parquet_sha256=record['parquet_sha256'],qc_sha256=record['qc_sha256'],
        preprocessing_version=signature,extractor_sha256=sha256(extractor),feature_contract=contract['feature_contract'])
    validate_feature_record(m,contract)
    m['qc_sha256']='f'*64
    with pytest.raises(ValueError,match='sources differ'):
        validate_feature_record(m,contract)

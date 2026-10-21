import numpy as np
import pandas as pd

from src.features.run import FS, WIN_N, aggregate_hours, make_windows, window_features


def test_extractor_rejects_missing_plus_duplicate_sample_even_if_count_is_500():
    time=np.arange(WIN_N)/FS
    xyz=np.column_stack([np.zeros(WIN_N),np.zeros(WIN_N),np.ones(WIN_N)])
    assert sum(len(starts) for starts,_ in make_windows(time,xyz))==1
    broken=time.copy()
    broken[10]=broken[9]
    assert list(make_windows(broken,xyz))==[]
    xyz[10,0]=np.nan
    assert list(make_windows(time,xyz))==[]


def test_static_signal_enmo_unit_and_absent_walking_are_not_invented():
    w=np.zeros((30,WIN_N,3),dtype=np.float32)
    w[:,:,2]=1.1
    features=window_features(w)
    assert np.allclose(features['enmo'],.1,atol=1e-6)  # g, not mg
    assert not features['walk'].any()
    assert not features['tremor'].any()
    win=pd.DataFrame(features)
    win['day'],win['hour'],win['hour_idx']=0,9,9
    row=aggregate_hours(win).iloc[0]
    assert row.n_windows==30 and row.valid_min==5
    assert pd.isna(row.step_freq_walk_median)
    assert row.tremor_frac_of_rest==0  # real zero on observed rest, not absent rest


def test_clean_without_original_run_evidence_is_blocked_before_extraction(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    import yaml
    from src.reference.common import ROOT,load_config
    from src.reference.io import create_bundle,load_bundle
    from tools.extract_reference_features import extract

    # Deliberately tiny SYNTHETIC sensor/QC fixture; no baseline is fit from it.
    cfg=load_config()
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    roster=pd.DataFrame([dict(dataset='cops',subject_id='SYNTHETIC-CLEAN',split='train')])
    time=np.arange(100)/50+9*3600
    table=pa.table(dict(time_s=time,x=np.zeros(100),y=np.zeros(100),z=np.ones(100),
                       valid=np.ones(100,dtype=bool),day=np.zeros(100,dtype=int)))
    parquet=tmp_path/'synthetic.parquet'
    pq.write_table(table,parquet)
    qc=tmp_path/'synthetic.qc.json'
    qc.write_text(json.dumps(dict(subject_id='SYNTHETIC-CLEAN',dataset='cops',split='train',
        fs_native=100,fs_out=50,status='ok',included=True,valid_days=3,
        days=[dict(day=d,valid=True,valid_h=10.) for d in [0,1,2]])))
    import pytest
    with pytest.raises(ValueError, match='Unverified historical clean provenance'):
        extract('cops','SYNTHETIC-CLEAN',parquet,qc,tmp_path/'hourly',roster,cfg,pipeline)
    assert not (tmp_path/'hourly').exists()

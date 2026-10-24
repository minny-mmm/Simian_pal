import copy
import json

import numpy as np
import pandas as pd
import pytest

from src.reference.common import fingerprint, load_config
from src.reference.daily import METRICS, aggregate_daily
from src.reference.io import create_bundle, load_bundle, write_bundle
from src.reference.model import compare_daily, fit_reference, load_reference, save_reference, summarize, weighted_quantile
from tools.reference_demo import synthetic_inputs


@pytest.fixture
def example():
    cfg=load_config()
    hourly,roster,provenance=synthetic_inputs(cfg)
    daily,hour_qc=aggregate_daily(hourly,roster,cfg,provenance)
    return cfg,hourly,roster,provenance,daily,hour_qc


def short_example():
    cfg=load_config()
    cfg['feature_policy'].update(min_hour_valid_minutes=0,min_day_valid_minutes=0,min_day_hours=1,
        min_metric_minutes=0,min_metric_hours=1,metric_coverage={})
    roster=pd.DataFrame([dict(dataset='cops',subject_id='SYN-A',split='train')])
    rows=[]
    for hour,n,enmo,walk,cadence,rest,tremor in [(9,60,.1,10,1.,10,1),(10,180,.3,30,2.,30,9)]:
        rows.append(dict(dataset='cops',subject_id='SYN-A',split='train',day=0,hour=hour,n_windows=n,
            valid_min=n/6,n_walk=walk,n_clean_walk=0,n_rest=rest,n_tremor=tremor,
            enmo_median=enmo,step_freq_walk_median=cadence,tremor_frac_of_rest=tremor/rest))
    provenance=dict(data_kind='synthetic',preprocessing_version='synthetic-hand-example',verified=True,
        config_hash=fingerprint(cfg),feature_contract=cfg['feature_contract'])
    return cfg,pd.DataFrame(rows),roster,provenance


def test_hand_daily_formulas_are_not_mean_of_ratios_or_pooled_median():
    cfg,f,roster,p=short_example()
    daily,_=aggregate_daily(f,roster,cfg,p)
    values=daily.set_index('metric').value
    assert values['enmo_hourly_median_mean_g']==pytest.approx((.1*60+.3*180)/240)
    assert values['walking_cadence_hourly_median_mean_hz']==pytest.approx(1.75)
    assert values['rest_tremor_window_fraction']==pytest.approx((1+9)/(10+30))
    assert values['rest_tremor_window_fraction']!=pytest.approx((.1+.3)/2)


def test_no_walking_or_rest_is_null_but_observed_no_tremor_is_zero():
    cfg,f,roster,p=short_example()
    f[['n_walk','n_clean_walk','n_rest','n_tremor']]=0
    f[['step_freq_walk_median','tremor_frac_of_rest']]=np.nan
    daily,_=aggregate_daily(f,roster,cfg,p)
    result=daily.set_index('metric')
    assert result.loc['enmo_hourly_median_mean_g','status']=='ok'
    for name in ['walking_cadence_hourly_median_mean_hz','rest_tremor_window_fraction']:
        assert pd.isna(result.loc[name,'value'])
        assert result.loc[name,'status']=='insufficient_data'
        assert 'no_eligible_windows' in result.loc[name,'reasons']
    f.n_rest=10
    f.tremor_frac_of_rest=0.
    daily,_=aggregate_daily(f,roster,cfg,p)
    assert daily.set_index('metric').loc['rest_tremor_window_fraction','value']==0.


def test_missing_indicator_keeps_per_indicator_coverage():
    cfg,f,roster,p=short_example()
    f.loc[0,'step_freq_walk_median']=np.nan
    daily,_=aggregate_daily(f,roster,cfg,p)
    row=daily.set_index('metric').loc['walking_cadence_hourly_median_mean_hz']
    assert row.value==2.
    assert row.metric_windows==30 and row.missing_metric_windows==10
    assert row.metric_eligible_hours==1


def test_qc_day_without_hourly_rows_is_explicit_insufficient_data():
    cfg,f,roster,p=short_example()
    daily,_=aggregate_daily(f,roster,cfg,p,expected_days=[dict(dataset='cops',subject_id='SYN-A',split='train',day=1)])
    absent=daily[daily.day.eq(1)]
    assert len(absent)==3 and absent.value.isna().all()
    assert absent.status.eq('insufficient_data').all()
    assert absent.day_eligible_hours.eq(0).all()


@pytest.mark.parametrize('failure',['duplicate','ratio','missing_walk_is_zero','split','counts','coverage'])
def test_invalid_hourly_data_rejected(failure):
    cfg,f,roster,p=short_example()
    if failure=='duplicate':
        f=pd.concat([f,f.iloc[:1]],ignore_index=True)
    elif failure=='ratio':
        f.loc[0,'tremor_frac_of_rest']=.9
    elif failure=='missing_walk_is_zero':
        f.loc[0,['n_walk','n_clean_walk','step_freq_walk_median']]=0
    elif failure=='split':
        f.loc[0,'split']='test'
    elif failure=='counts':
        f.loc[0,'n_rest']=999
    else:
        f.loc[0,'valid_min']=59
    with pytest.raises(ValueError):
        aggregate_daily(f,roster,cfg,p)


def test_hand_reference_equal_subject_weights_and_mad():
    entries=[dict(subject_id=s,day=d,value=v) for s,d,v in [('A',0,1.),('A',1,3.),('B',0,10.),('C',0,20.)]]
    policy=dict(min_days_per_participant=1,min_participants=3,min_days=4,mad_scale_factor=1.4826,quantiles=[.05,.25,.5,.75,.95])
    s=summarize(entries,policy)
    # A has weights .5,.5, B and C weight 1 each: normalized masses 1/6,1/6,1/3,1/3.
    assert s['median']==10 and s['mad']==7
    assert s['scale']==pytest.approx(10.3782)
    assert s['quantiles']['0.25']==3
    assert s['n_participants']==3 and s['n_days']==4
    assert (20-s['median'])/s['scale']==pytest.approx(.963558224)


def test_inverse_cdf_boundary_is_stable_without_normalization_roundoff():
    assert weighted_quantile(np.arange(1.,13.),np.full(12,.5),.5)==6.
    assert weighted_quantile(np.arange(1.,19.),np.full(18,1/3),.5)==9.


def test_train_person_is_excluded_from_all_own_days_and_reweighted(example):
    cfg,_,roster,p,daily,_=example
    artifact=fit_reference(daily,roster,cfg,p)
    sid=roster[roster.dataset.eq('cops') & roster.split.eq('train')].iloc[0].subject_id
    query=daily[daily.subject_id.eq(sid)]
    out=compare_daily(query,artifact)
    for result in out:
        assert result['reference_comparisons']['cops']['n_participants']==5
        assert result['reference_comparisons']['cops']['n_days']==10
        assert result['reference_comparisons']['cops']['excluded_train_subject']==sid
        assert result['reference_comparisons']['nhanes']['n_participants']==6
        metric=result['metric']
        expected=summarize([e for e in artifact['references']['cops'][metric]['entries'] if e['subject_id']!=sid],cfg['reference_policy'])
        assert result['reference_comparisons']['cops']['reference_median']==expected['median']


def test_valid_test_never_change_fitted_references(example):
    cfg,_,roster,p,daily,_=example
    a=fit_reference(daily,roster,cfg,p)
    changed=daily.copy()
    changed.loc[changed.split.ne('train') & changed.metric.eq('enmo_hourly_median_mean_g'),'value']=9999.
    b=fit_reference(changed,roster,cfg,p)
    assert a['references']==b['references']
    assert a['training_daily_hash']==b['training_daily_hash']
    for r in compare_daily(changed[changed.split.ne('train')],a):
        assert r['reference_comparisons']['cops']['excluded_train_subject'] is None
        assert r['reference_comparisons']['cops']['n_participants']==6


def test_zero_scale_keeps_real_unit_distance_and_null_standardization(example):
    cfg,_,roster,p,daily,_=example
    daily.loc[daily.metric.eq('enmo_hourly_median_mean_g'),'value']=.1
    artifact=fit_reference(daily,roster,cfg,p)
    query=daily[daily.split.eq('valid') & daily.metric.eq('enmo_hourly_median_mean_g')].copy()
    query.value=.2
    result=compare_daily(query,artifact)[0]['reference_comparisons']['cops']
    assert result['status']=='zero_scale'
    assert result['signed_distance']==pytest.approx(.1)
    assert result['standardized_deviation'] is None


def test_insufficient_reference_and_daily_are_not_fabricated_scores(example):
    cfg,_,roster,p,daily,_=example
    tiny=daily[daily.subject_id.isin(roster[roster.split.eq('train')].subject_id.iloc[:1])]
    artifact=fit_reference(tiny,roster,cfg,p)
    query=daily[daily.split.eq('valid')].iloc[:1]
    assert compare_daily(query,artifact)[0]['reference_comparisons']['nhanes']['status']=='insufficient_data'
    bad=query.copy()
    bad['value']=np.nan
    bad['status']='insufficient_data'
    bad['reasons']='no_eligible_windows'
    result=compare_daily(bad,artifact)[0]['reference_comparisons']['cops']
    assert result['status']=='insufficient_data' and result['signed_distance'] is None


def test_artifact_roundtrip_tampering_and_contracts(tmp_path,example):
    cfg,_,roster,p,daily,_=example
    artifact=fit_reference(daily,roster,cfg,p)
    path=tmp_path/'baseline.json'
    save_reference(path,artifact)
    loaded=load_reference(path)
    assert compare_daily(daily,loaded)==compare_daily(daily,artifact)
    payload=json.loads(path.read_text())
    payload['fit_split']='test'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_reference(path)
    wrong=daily.copy()
    wrong['unit']='mg'
    with pytest.raises(ValueError,match='unit'):
        fit_reference(wrong,roster,cfg,p)
    with pytest.raises(ValueError,match='Unverified'):
        fit_reference(daily,roster,cfg,{**p,'verified':False})


def test_bundle_hashes_and_unversioned_features_blocked(tmp_path,example):
    cfg,_,roster,p,daily,hour_qc=example
    out=tmp_path/'bundle'
    write_bundle(out,daily,hour_qc,roster,cfg,{},p)
    assert len(load_bundle(out)[0])==len(daily)
    (out/'daily.csv').write_text('tampered')
    with pytest.raises(ValueError,match='hash'):
        load_bundle(out)
    legacy=tmp_path/'legacy'
    legacy.mkdir()
    (legacy/'patient.csv').write_text('subject_id\nSYN-A\n')
    with pytest.raises(ValueError,match='provenance'):
        create_bundle(legacy,tmp_path/'out',roster,cfg,{}, {})


def test_real_fit_cannot_accept_legacy_verified_boolean_without_clean_run_evidence(example):
    cfg,_,roster,p,daily,_=example
    real=daily.copy()
    real['data_kind']='real'
    with pytest.raises(ValueError,match='historical clean provenance'):
        fit_reference(real,roster,cfg,{**p,'data_kind':'real','verified':True})

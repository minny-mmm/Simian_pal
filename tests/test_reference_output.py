import copy
import json
import subprocess
import sys

import pandas as pd
import pytest
from jsonschema import ValidationError

from src.reference.common import ROOT, load_config
from src.reference.daily import aggregate_daily
from src.reference.model import fit_reference
from src.reference.output import (build_analysis, build_expected_explanation, schemas,
                                  validate_explanation, validate_output)
from tools.reference_demo_review import review_inputs, run_review_demo


@pytest.fixture
def analysis_case():
    cfg=load_config()
    cfg.update(version='reference-baseline-demo-v1',baseline_version='demo-cohort-reference-v1')
    hourly,roster,p,expected=review_inputs(cfg)
    daily,_=aggregate_daily(hourly,roster,cfg,p,expected_days=expected)
    model=fit_reference(daily[daily.split.eq('train')],roster,cfg,p)
    analysis=build_analysis(daily[daily.subject_id.eq('DEMO-001')],model)
    return analysis,daily,roster,cfg,p,model


def test_hand_values_missing_context_and_consecutive_days(analysis_case):
    analysis,_,_,_,_,model=analysis_case
    rows={(r['day'],r['metric']):r for r in analysis['analysis']}
    enmo='enmo_hourly_median_mean_g'
    cadence='walking_cadence_hourly_median_mean_hz'
    tremor='rest_tremor_window_fraction'
    assert len(rows)==30
    assert rows[0,enmo]['value']==pytest.approx(.03875)
    assert rows[0,cadence]['value']==pytest.approx(1.45)
    assert rows[0,tremor]['value']==pytest.approx(42/720)
    assert rows[1,enmo]['previous_day']['delta']==pytest.approx(.002)
    assert rows[0,enmo]['previous_day']['status']=='no_previous_day'
    assert rows[2,cadence]['qc']['measurement_state']=='no_walking_windows'
    assert rows[2,cadence]['value'] is None
    assert rows[3,tremor]['qc']['measurement_state']=='no_rest_windows'
    assert rows[3,tremor]['value'] is None
    assert rows[4,enmo]['qc']['measurement_state']=='no_observations'
    assert rows[5,enmo]['previous_day']['status']=='previous_day_missing'
    assert rows[5,enmo]['previous_day']['delta'] is None
    assert all(rows[6,m]['value'] is None for m in [enmo,cadence,tremor])
    assert rows[7,tremor]['value']==0
    assert rows[8,enmo]['value'] is None and rows[8,cadence]['value'] is not None
    assert rows[9,enmo]['previous_day']['status']=='previous_day_insufficient_data'
    assert rows[9,enmo]['previous_day']['delta'] is None
    for ds,refs in model['references'].items():
        for ref in refs.values():
            assert ref['summary']['n_participants']==6 and ref['summary']['n_days']==18
            assert 'DEMO-001' not in {e['subject_id'] for e in ref['entries']}
    for r in analysis['analysis']:
        for ds,c in r['reference_comparisons'].items():
            if r['value'] is None:
                assert c['signed_distance'] is None
            else:
                median=model['references'][ds][r['metric']]['summary']['median']
                assert c['signed_distance']==pytest.approx(r['value']-median)


def test_schema_rejects_wrong_units_extra_fields_wrong_null_and_arithmetic(analysis_case):
    analysis,*_=analysis_case
    mutations=[lambda r:r.update(unit='mg'),lambda r:r.update(unknown_field=1),
               lambda r:r.update(value=None),lambda r:r['reference_comparisons']['cops'].update(signed_distance=999),
               lambda r:r['qc'].update(reasons=['fabricated_reason']),lambda r:r.update(value=float('inf'))]
    for mutate in mutations:
        broken=copy.deepcopy(analysis)
        mutate(broken['analysis'][0])
        with pytest.raises((ValidationError,ValueError)):
            validate_output(broken,'analysis')
    broken=copy.deepcopy(analysis)
    # Changing the public envelope to a real shape is schema testing only, never a real fit.
    broken.update(data_origin='real',data_kind='real',is_demo=False,baseline_version='reference-baseline-v1')
    validate_output(broken,'analysis')
    broken['is_demo']=True
    with pytest.raises(ValidationError):
        validate_output(broken,'analysis')


def test_expected_evidence_and_missing_previous_record_not_last_seen_day(analysis_case):
    analysis,daily,roster,cfg,p,model=analysis_case
    explanation=build_expected_explanation(analysis)
    assert explanation['generation_kind']=='expected_template' and explanation['model_version'] is None
    assert all('ไม่ใช่คำตอบ LLM' in e['text'] for e in explanation['explanations'])
    explanation['explanations'][0]['evidence']['value']=999
    with pytest.raises(ValueError,match='evidence'):
        validate_explanation(explanation,analysis)
    query=daily[daily.subject_id.eq('DEMO-001') & daily.day.isin([0,2])]
    result=build_analysis(query,model)
    for row in result['analysis']:
        if row['day']==2:
            assert row['previous_day']['status']=='previous_day_missing'
            assert row['previous_day']['delta'] is None


def test_real_fit_gate_unchanged_and_zero_scale_contract(analysis_case):
    _,daily,roster,cfg,p,_=analysis_case
    real=daily.copy()
    real['data_kind']='real'
    with pytest.raises(ValueError,match='historical clean provenance'):
        fit_reference(real,roster,cfg,{**p,'data_kind':'real'})
    daily.loc[daily.metric.eq('enmo_hourly_median_mean_g') & daily.status.eq('ok'),'value']=.1
    model=fit_reference(daily,roster,cfg,p)
    result=build_analysis(daily[daily.subject_id.eq('DEMO-001')],model)
    for row in result['analysis']:
        if row['metric']=='enmo_hourly_median_mean_g' and row['status']=='ok':
            assert row['reference_comparisons']['cops']['status']=='zero_scale'
            assert row['reference_comparisons']['cops']['standardized_deviation'] is None


def test_review_files_are_labelled_validated_and_cli_uses_same_contract(tmp_path):
    out=tmp_path/'demo'
    run_review_demo(out)
    for path in out.rglob('*.json'):
        doc=json.loads(path.read_text(encoding='utf-8'))
        assert doc['is_demo'] is True and doc['data_origin']=='synthetic' and 'demo' in doc['baseline_version'],path
    for path in out.rglob('*.csv'):
        frame=pd.read_csv(path)
        assert frame.is_demo.all() and frame.data_origin.eq('synthetic').all() and frame.baseline_version.str.contains('demo').all(),path
    assert not (out/'explanation_MODEL.json').exists()
    page=(out/'preview.html').read_text(encoding='utf-8')
    assert 'ข้อมูลจำลอง' in page and '<svg' in page and '<script' not in page and 'https://' not in page
    proc=subprocess.run([sys.executable,str(ROOT/'tools/reference_baseline.py'),'compare','--bundle',str(out/'query_bundle'),
        '--baseline',str(out/'model/baseline.json'),'--out',str(tmp_path/'analysis.json')],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr
    validate_output(json.loads((tmp_path/'analysis.json').read_text(encoding='utf-8')),'analysis')
    with pytest.raises(ValueError,match='exists'):
        run_review_demo(out)

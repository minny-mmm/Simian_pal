"""Versioned public analysis contract, shared by real and synthetic runs.

Adds consecutive-day comparisons to the existing compare_daily implementation.
It never fits a reference or relaxes real-data provenance checks.
"""
from copy import deepcopy
import math
import json

from jsonschema import Draft202012Validator

from .common import clean_json, fingerprint
from .daily import METRICS
from .model import compare_daily

SCHEMA_VERSION = '1.0.0'
NUMBER = {'type': ['number', 'null']}
NONNEG = {'type': 'number', 'minimum': 0}
INTEGER = {'type': 'integer', 'minimum': 0}
STRING = {'type': 'string'}
STATUS = {'enum': ['ok', 'insufficient_data']}


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def metric_rules():
    return [
        {'if': {'properties': {'metric': {'const': name}}},
         'then': {'properties': {'unit': {'const': definition['unit']},
                                'value': {'type': ['number','null'], 'minimum': 0,
                                          **({'maximum': 1} if name == 'rest_tremor_window_fraction' else {}),
                                          **({'exclusiveMinimum': 0} if 'cadence' in name else {})}}}}
        for name, definition in METRICS.items()]


def output_metadata(artifact):
    synthetic = artifact['data_kind'] == 'synthetic'
    version = artifact.get('baseline_version', ('demo-' if synthetic else '') + artifact['config']['version'])
    return dict(schema_version=SCHEMA_VERSION, is_demo=synthetic, data_origin=artifact['data_kind'],
                data_kind=artifact['data_kind'], baseline_version=version)


def schemas():
    metadata = dict(schema_version={'const': SCHEMA_VERSION}, is_demo={'type':'boolean'},
        data_origin={'enum':['real','synthetic']},data_kind={'enum':['real','synthetic']},baseline_version=STRING)
    identity = dict(subject_id=STRING,dataset={'enum':['cops','nhanes']},split={'enum':['train','valid','test']},
                    day=INTEGER,metric={'enum':list(METRICS)},unit={'enum':sorted({v['unit'] for v in METRICS.values()})})
    coverage = obj(dict(day_valid_minutes=NONNEG,day_eligible_hours=INTEGER,metric_minutes=NONNEG,
                        metric_windows=INTEGER,metric_eligible_hours=INTEGER,missing_metric_windows=INTEGER))
    reference = obj(dict(status={'enum':['ok','zero_scale','insufficient_data']},reason=STRING,
        signed_distance=NUMBER,absolute_distance={'type':['number','null'],'minimum':0},unit=identity['unit'],
        standardized_deviation=NUMBER,reference_median=NUMBER,
        reference_scale={'type':['number','null'],'minimum':0},reference_mad={'type':['number','null'],'minimum':0},
        reference_quantiles={'type':'object','propertyNames':{'pattern':r'^(0(\.\d+)?|1(\.0+)?)$'},
                             'additionalProperties':{'type':'number'}},
        n_participants=INTEGER,n_days=INTEGER,excluded_train_subject={'type':['string','null']}))
    reference['allOf']=[
        {'if':{'properties':{'status':{'const':'zero_scale'}}},
         'then':{'properties':{'reference_scale':{'const':0},'standardized_deviation':{'type':'null'}}}},
        {'if':{'properties':{'status':{'const':'insufficient_data'}}},
         'then':{'properties':{'signed_distance':{'type':'null'},'absolute_distance':{'type':'null'},
                               'standardized_deviation':{'type':'null'}}}}]
    previous = obj(dict(day={'type':['integer','null'],'minimum':0},value=NUMBER,unit=identity['unit'],delta=NUMBER,
        status={'enum':['ok','no_previous_day','previous_day_missing','previous_day_insufficient_data',
                        'current_day_insufficient_data']},reason=STRING))
    previous['allOf']=[{'if':{'properties':{'status':{'const':'ok'}}},
        'then':{'properties':{'delta':{'type':'number'},'value':{'type':'number'},'day':INTEGER}},
        'else':{'properties':{'delta':{'type':'null'},'reason':{'minLength':1}}}}]
    qc = obj(dict(status=STATUS,reasons={'type':'array','items':STRING},
        observed_hour_rows=INTEGER,measurement_state={'enum':['observed','no_observations','no_walking_windows',
            'no_rest_windows','insufficient_coverage','missing_metric_values']}))
    row = obj(dict(**identity,value=NUMBER,status=STATUS,reporting_clock_hours={'type':'array',
        'prefixItems':[INTEGER,INTEGER],'minItems':2,'maxItems':2},daily_definition=STRING,coverage=coverage,
        reference_comparisons=obj(dict(cops=deepcopy(reference),nhanes=deepcopy(reference))),
        artifact_id=STRING,interpretation={'const':'distance_from_cohort_reference_only'},qc=qc,previous_day=previous))
    row['allOf']=metric_rules()+[
        {'if':{'properties':{'status':{'const':'ok'}}},'then':{'properties':{'value':{'type':'number'}}},
         'else':{'properties':{'value':{'type':'null'},'qc':{'properties':{'reasons':{'minItems':1}}}}}}]
    analysis=obj(dict(**metadata,analysis={'type':'array','items':row}))
    daily_row=obj(dict(**identity,value=NUMBER,status=STATUS,reasons=STRING,
        **{key:INTEGER for key in ['day_eligible_hours','observed_hour_rows','metric_eligible_hours','metric_windows',
                                  'available_metric_windows','missing_metric_windows']},
        day_valid_minutes=NONNEG,metric_minutes=NONNEG,feature_contract=STRING,config_hash=STRING,
        preprocessing_version=STRING,data_kind=metadata['data_kind']))
    daily_row['allOf']=metric_rules()+[{'if':{'properties':{'status':{'const':'ok'}}},
        'then':{'properties':{'value':{'type':'number'}}},
        'else':{'properties':{'value':{'type':'null'},'reasons':{'minLength':1}}}}]
    daily=obj(dict(**metadata,daily={'type':'array','items':daily_row}))
    explanation_row=obj(dict(**identity,text=STRING,analysis_row_id=STRING,
        evidence=obj(dict(value=NUMBER,cops_signed_distance=NUMBER,nhanes_signed_distance=NUMBER,
            previous_day_delta=NUMBER,qc_status=STATUS))))
    explanation=obj(dict(**metadata,generation_kind={'enum':['expected_template','llm']},
        model_version={'type':['string','null']},prompt_version=STRING,analysis_sha256=STRING,
        explanations={'type':'array','items':explanation_row}))
    explanation['allOf']=[{'if':{'properties':{'generation_kind':{'const':'expected_template'}}},
        'then':{'properties':{'model_version':{'type':'null'}}},
        'else':{'properties':{'model_version':{'type':'string','minLength':1}}}}]
    summary=obj(dict(status={'enum':['ok','zero_scale','insufficient_data']},reason=STRING,
        n_participants=INTEGER,n_days=INTEGER,subject_ids={'type':'array','items':STRING},median=NUMBER,
        quantiles=reference['properties']['reference_quantiles'],mad=NUMBER,scale=NUMBER,
        days_per_subject={'type':'object','additionalProperties':INTEGER}))
    refs={}
    for name,definition in METRICS.items():
        refs[name]=obj(dict(unit={'const':definition['unit']},entries={'type':'array',
            'items':obj(dict(subject_id=STRING,day=INTEGER,value={'type':'number'}))},summary=summary))
    roster_row=obj(dict(dataset=identity['dataset'],subject_id=STRING,split=identity['split'],**metadata))
    roster_row['required']=['dataset','subject_id','split']
    baseline=obj(dict(schema_version={'const':1},kind={'const':'separate_cohort_reference_not_diagnosis'},
        created_utc=STRING,data_kind=metadata['data_kind'],fit_split={'const':'train'},config={'type':'object'},
        config_hash=STRING,metrics={'type':'object'},preprocessing_version=STRING,provenance={'type':'object'},
        code_version={'type':'object'},roster={'type':'array','items':roster_row},roster_hash=STRING,
        training_daily_hash=STRING,method={'type':'object'},references=obj(dict(cops=obj(deepcopy(refs)),nhanes=obj(deepcopy(refs)))),
        artifact_id=STRING,is_demo={'type':'boolean'},data_origin=metadata['data_origin'],baseline_version=STRING))
    hourly_fields={k:identity[k] for k in ['subject_id','dataset','split','day']}
    hourly_fields.update(hour={'type':'integer','minimum':0,'maximum':23},valid_min=NONNEG,
        **{k:INTEGER for k in ['n_windows','n_walk','n_clean_walk','n_rest','n_tremor']},
        enmo_median={'type':['number','null'],'minimum':0},
        step_freq_walk_median={'type':['number','null'],'exclusiveMinimum':0},
        tremor_frac_of_rest={'type':['number','null'],'minimum':0,'maximum':1})
    hourly=obj(dict(**metadata,units=obj(dict(enmo_median={'const':'g'},step_freq_walk_median={'const':'steps/s'},
        tremor_frac_of_rest={'const':'fraction'},valid_min={'const':'minutes'})),hourly={'type':'array','items':obj(hourly_fields)}))
    result=dict(analysis=analysis,daily=daily,explanation=explanation,hourly=hourly,baseline=baseline)
    for name,schema in result.items():
        schema.update({'$schema':'https://json-schema.org/draft/2020-12/schema',
            '$id':f'urn:parkinsons:reference:{name}:{SCHEMA_VERSION}',
            'description':'Same contract for real and synthetic data. Null means unavailable, never zero. Days are zero-based recording-day indices.'})
        schema.setdefault('allOf',[]).extend([
            {'if':{'properties':{'data_origin':{'const':'synthetic'}}},
             'then':{'properties':{'is_demo':{'const':True},'data_kind':{'const':'synthetic'},
                                    'baseline_version':{'pattern':'demo'}}},
             'else':{'properties':{'is_demo':{'const':False},'data_kind':{'const':'real'}}}}])
        Draft202012Validator.check_schema(schema)
    return result


def validate_output(document, kind):
    json.dumps(document,allow_nan=False)  # reject NaN/Infinity, which JSON Schema numbers alone cannot exclude
    Draft202012Validator(schemas()[kind]).validate(document)
    # JSON Schema fixes shape/units/types. Cross-field arithmetic needs a separate check.
    if kind != 'analysis':
        return
    seen=set()
    for r in document['analysis']:
        key=tuple(r[k] for k in ['dataset','subject_id','day','metric'])
        if key in seen:
            raise ValueError('Duplicate analysis metric-day')
        seen.add(key)
        if r['qc']['status'] != r['status'] or r['previous_day']['unit'] != r['unit']:
            raise ValueError('QC/unit inconsistency')
        if (r['status']=='ok') != (len(r['qc']['reasons'])==0):
            raise ValueError('QC reasons inconsistent with status')
        for ref in r['reference_comparisons'].values():
            if ref['unit'] != r['unit']:
                raise ValueError('Reference unit differs from indicator')
            if r['status']!='ok' and any(ref[k] is not None for k in ['signed_distance','absolute_distance','standardized_deviation']):
                raise ValueError('Unavailable observation must have null comparisons')
            if r['status']=='ok' and ref['status'] in ['ok','zero_scale']:
                if ref['signed_distance'] is None or ref['reference_median'] is None or ref['reference_scale'] is None:
                    raise ValueError('Available comparison must contain distance, median and scale')
                if ref['status']=='ok' and ref['reference_scale']<=0:
                    raise ValueError('OK reference requires positive scale')
            if ref['signed_distance'] is not None:
                expected=r['value']-ref['reference_median']
                if not math.isclose(expected,ref['signed_distance'],rel_tol=1e-12,abs_tol=1e-12) or not math.isclose(abs(expected),ref['absolute_distance'],rel_tol=1e-12,abs_tol=1e-12):
                    raise ValueError('Incorrect cohort distance')
                scale=ref['reference_scale']
                if scale and scale>0:
                    if ref['standardized_deviation'] is None or not math.isclose(expected/scale,ref['standardized_deviation'],rel_tol=1e-12,abs_tol=1e-12):
                        raise ValueError('Incorrect standardized deviation')
        previous=r['previous_day']
        if previous['status']=='ok' and (previous['day'] != r['day']-1 or not math.isclose(
                r['value']-previous['value'],previous['delta'],rel_tol=1e-12,abs_tol=1e-12)):
            raise ValueError('Incorrect consecutive-day difference')


def build_analysis(daily, artifact):
    rows=compare_daily(daily,artifact)
    lookup={(r['dataset'],r['subject_id'],r['day'],r['metric']):r for r in daily.to_dict('records')}
    for row in rows:
        key=(row['dataset'],row['subject_id'],row['day'],row['metric'])
        raw=lookup[key]
        reasons=str(raw['reasons']).split('|') if raw['status']!='ok' else []
        observed=int(raw['observed_hour_rows'])
        state='observed'
        if not observed:
            state='no_observations'
        elif row['status']!='ok':
            if not raw['available_metric_windows'] and row['metric']=='walking_cadence_hourly_median_mean_hz':
                state='no_walking_windows'
            elif not raw['available_metric_windows'] and row['metric']=='rest_tremor_window_fraction':
                state='no_rest_windows'
            elif 'metric_values_missing' in reasons or raw['missing_metric_windows']:
                state='missing_metric_values'
            else:
                state='insufficient_coverage'
        row['qc']=dict(status=row['status'],reasons=reasons,observed_hour_rows=observed,measurement_state=state)
        prior=lookup.get((key[0],key[1],key[2]-1,key[3]))
        prior_day=key[2]-1 if key[2]>0 else None
        if not key[2]:
            status='no_previous_day'
        elif prior is None or not prior['observed_hour_rows']:
            status='previous_day_missing'
        elif row['status']!='ok':
            status='current_day_insufficient_data'
        elif prior['status']!='ok':
            status='previous_day_insufficient_data'
        else:
            status='ok'
        row['previous_day']=dict(day=prior_day,value=clean_json(prior['value']) if prior else None,
            unit=row['unit'],delta=row['value']-prior['value'] if status=='ok' else None,
            status=status,reason='' if status=='ok' else status)
    document=dict(**output_metadata(artifact),analysis=clean_json(rows))
    validate_output(document,'analysis')
    return document


def row_id(row):
    return '/'.join(str(row[k]) for k in ['dataset','subject_id','day','metric'])


def build_expected_explanation(analysis):
    validate_output(analysis,'analysis')
    rows=[]
    def number(value):
        return 'null' if value is None else format(value,'.8g')
    for row in analysis['analysis']:
        evidence=dict(value=row['value'],cops_signed_distance=row['reference_comparisons']['cops']['signed_distance'],
            nhanes_signed_distance=row['reference_comparisons']['nhanes']['signed_distance'],
            previous_day_delta=row['previous_day']['delta'],qc_status=row['status'])
        prefix='ข้อมูลจำลอง; expected/template จากโค้ด ไม่ใช่คำตอบ LLM. ' if analysis['is_demo'] else 'Expected/template จากโค้ด ไม่ใช่คำตอบ LLM. '
        if row['value'] is None:
            text=prefix+f"วันที่ {row['day']+1}: {row['metric']} วัดไม่ได้ (null); QC={row['qc']['measurement_state']}; เหตุผล={','.join(row['qc']['reasons'])}."
        else:
            text=prefix+f"วันที่ {row['day']+1}: {row['metric']}={number(row['value'])} {row['unit']}; ผลต่างจากค่ามัธยฐาน COPS={number(evidence['cops_signed_distance'])}, NHANES={number(evidence['nhanes_signed_distance'])} {row['unit']} (ค่าบวกสูงกว่า ค่าลบต่ำกว่า)."
        text+=f" ผลต่างจากวันก่อน={number(evidence['previous_day_delta'])} {row['unit']}; สถานะ={row['previous_day']['status']}. เป็นระยะห่างจากกลุ่มอ้างอิง ไม่ใช่คะแนนความรุนแรงหรือโอกาสเป็นโรค."
        rows.append(dict(**{k:row[k] for k in ['subject_id','dataset','split','day','metric','unit']},
                         text=text,analysis_row_id=row_id(row),evidence=evidence))
    document=dict(**{k:analysis[k] for k in ['schema_version','is_demo','data_origin','data_kind','baseline_version']},
        generation_kind='expected_template',model_version=None,prompt_version='reference-expected-template-v1',
        analysis_sha256=fingerprint(analysis),explanations=rows)
    validate_explanation(document,analysis)
    return document


def validate_explanation(document,analysis):
    validate_output(document,'explanation')
    if document['analysis_sha256'] != fingerprint(analysis):
        raise ValueError('Explanation targets a different analysis')
    lookup={row_id(r):r for r in analysis['analysis']}
    ids=[r['analysis_row_id'] for r in document['explanations']]
    if len(ids)!=len(set(ids)) or set(ids)!=set(lookup):
        raise ValueError('Explanation rows do not match analysis')
    for e in document['explanations']:
        r=lookup[e['analysis_row_id']]
        expected=dict(value=r['value'],cops_signed_distance=r['reference_comparisons']['cops']['signed_distance'],
            nhanes_signed_distance=r['reference_comparisons']['nhanes']['signed_distance'],
            previous_day_delta=r['previous_day']['delta'],qc_status=r['status'])
        if e['evidence']!=expected or any(e[k]!=r[k] for k in ['subject_id','dataset','split','day','metric','unit']):
            raise ValueError('Explanation evidence differs from analysis')
    for key in ['schema_version','is_demo','data_origin','data_kind','baseline_version']:
        if document[key]!=analysis[key]:
            raise ValueError('Explanation metadata differs from analysis')

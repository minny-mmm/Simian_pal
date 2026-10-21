"""Ten-day SYNTHETIC review fixtures and local preview using the existing baseline."""
from copy import deepcopy
import html
from pathlib import Path

import pandas as pd
import yaml

from src.reference.common import ROOT, clean_json, code_version, fingerprint, load_config, sha256, write_json
from src.reference.daily import METRICS, aggregate_daily
from src.reference.io import write_bundle
from src.reference.model import load_reference
from src.reference.output import (SCHEMA_VERSION, build_analysis, build_expected_explanation,
                                  schemas, validate_explanation, validate_output)
from tools.reference_baseline import fit_bundle

SCENARIOS = ['ข้อมูลครบ / วันแรก','ข้อมูลครบ / เทียบวันก่อน','ไม่มีช่วงเดิน','ไม่มีช่วงพัก',
             'ไม่มีข้อมูลทั้งวัน','ข้อมูลครบ / วันก่อนขาด','ข้อมูลไม่พอ: มีเพียง 2 ชั่วโมง',
             'มีช่วงพักและ tremor=0 ที่วัดได้','ENMO บางชั่วโมงหาย','ข้อมูลครบ / ENMO วันก่อนไม่พอ']


def review_inputs(cfg):
    roster, rows = [], []
    for ds in ['cops','nhanes']:
        for p in range(1,7):
            sid=f'DEMO-REF-{ds.upper()}-{p:03d}'
            roster.append(dict(dataset=ds,subject_id=sid,split='train'))
            # Unequal days demonstrate equal total participant weights, not pooled-day weighting.
            for day in range(2+p%3):
                for hour in range(9,15):
                    tremor=p+day+2 if ds=='cops' else p//2+day%2
                    rows.append(dict(dataset=ds,subject_id=sid,split='train',day=day,hour=hour,
                        n_windows=360,valid_min=60.,n_walk=60,n_clean_walk=30,n_rest=120,n_tremor=tremor,
                        enmo_median=(.025 if ds=='cops' else .055)+.003*p+.001*day+.0001*(hour-9),
                        step_freq_walk_median=(1.3 if ds=='cops' else 1.65)+.04*p+.01*day,
                        tremor_frac_of_rest=tremor/120))
    roster.append(dict(dataset='cops',subject_id='DEMO-001',split='test'))
    for day in range(10):
        if day==4:
            continue
        for hour in range(9,11 if day==6 else 15):
            walk=0 if day==2 else 60
            rest=0 if day==3 else 120
            tremor=0 if day in [3,7] else 7+day
            rows.append(dict(dataset='cops',subject_id='DEMO-001',split='test',day=day,hour=hour,
                n_windows=360,valid_min=60.,n_walk=walk,n_clean_walk=30 if walk else 0,
                n_rest=rest,n_tremor=tremor,
                enmo_median=None if day==8 and hour==13 else .038+.002*day+.0003*(hour-9),
                step_freq_walk_median=1.45+.015*day if walk else None,
                tremor_frac_of_rest=tremor/rest if rest else None))
    meta=dict(schema_version=SCHEMA_VERSION,is_demo=True,data_origin='synthetic',data_kind='synthetic',
              baseline_version=cfg['baseline_version'])
    provenance=dict(**meta,verified=True,preprocessing_version='demo-synthetic-hourly-fixture-v1',
        feature_contract=cfg['feature_contract'],config_hash=fingerprint(cfg),
        note='Invented hourly inputs, not sensor extraction or historical clean provenance. Real-data validation unchanged.')
    expected=[dict(dataset='cops',subject_id='DEMO-001',split='test',day=d) for d in range(10)]
    return pd.DataFrame(rows),pd.DataFrame(roster),provenance,expected


def preview_html(analysis, explanations, artifact):
    esc=html.escape
    def fmt(v):
        return 'null' if v is None else f'{v:.6g}'
    def chart(rows,metric,unit):
        ref={ds:artifact['references'][ds][metric]['summary'] for ds in ['cops','nhanes']}
        values=[r['value'] for r in rows if r['value'] is not None]
        values += [v for s in ref.values() for v in s['quantiles'].values()]
        low,high=min(values),max(values)
        pad=(high-low)*.15 or .01
        low,high=low-pad,high+pad
        x=lambda d:65+d*75
        y=lambda v:235-(v-low)/(high-low)*195
        svg=['<svg viewBox="0 0 820 285" role="img" aria-label="ค่ารายวันและ median/IQR ของกลุ่มอ้างอิงจำลอง">']
        for i in range(5):
            value=low+(high-low)*i/4
            yy=y(value)
            svg.append(f'<line x1="65" x2="760" y1="{yy}" y2="{yy}" stroke="#e2e8f0"/><text x="58" y="{yy+4}" text-anchor="end" font-size="11">{fmt(value)}</text>')
        for ds,color in [('cops','#be3455'),('nhanes','#14805e')]:
            s=ref[ds]
            q1,q3=s['quantiles']['0.25'],s['quantiles']['0.75']
            svg.append(f'<rect x="65" y="{y(q3)}" width="675" height="{y(q1)-y(q3)}" fill="{color}" opacity=".10"/>')
            svg.append(f'<line x1="65" x2="740" y1="{y(s["median"])}" y2="{y(s["median"])}" stroke="{color}" stroke-dasharray="7 4"><title>{ds.upper()} median {fmt(s["median"])} {unit}</title></line>')
        previous=None
        for r in rows:
            xx=x(r['day'])
            svg.append(f'<text x="{xx}" y="258" text-anchor="middle" font-size="12">{r["day"]+1}</text>')
            if r['value'] is None:
                previous=None
                svg.append(f'<text x="{xx}" y="278" text-anchor="middle" fill="#9a3412" font-size="11">ขาด</text>')
                continue
            yy=y(r['value'])
            if previous:
                svg.append(f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{xx}" y2="{yy}" stroke="#255ec7" stroke-width="2"/>')
            svg.append(f'<circle cx="{xx}" cy="{yy}" r="4" fill="#255ec7"><title>วันที่ {r["day"]+1}: {fmt(r["value"])} {unit}</title></circle>')
            previous=(xx,yy)
        return ''.join(svg)+'</svg>'
    sections=[]
    labels={'enmo_hourly_median_mean_g':'ENMO: ค่าเฉลี่ยถ่วงน้ำหนักของ median รายชั่วโมง',
            'walking_cadence_hourly_median_mean_hz':'Cadence ขณะเดิน',
            'rest_tremor_window_fraction':'สัดส่วนหน้าต่างที่เข้าเกณฑ์ tremor ในช่วงพัก'}
    for metric,definition in METRICS.items():
        rows=sorted([r for r in analysis['analysis'] if r['metric']==metric],key=lambda r:r['day'])
        refs=artifact['references']
        description='; '.join(f"{ds.upper()}: {refs[ds][metric]['summary']['n_participants']} คน / {refs[ds][metric]['summary']['n_days']} วัน"
                              for ds in ['cops','nhanes'])
        table=[]
        for r in rows:
            c,n=r['reference_comparisons']['cops'],r['reference_comparisons']['nhanes']
            cells=[str(r['day']+1),SCENARIOS[r['day']],fmt(r['value']),fmt(c['signed_distance']),fmt(c['standardized_deviation']),
                   fmt(n['signed_distance']),fmt(n['standardized_deviation']),fmt(r['previous_day']['delta']),
                   r['qc']['measurement_state']+'; '+','.join(r['qc']['reasons']),r['previous_day']['status']]
            table.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in cells)+'</tr>')
        texts=[e for e in explanations['explanations'] if e['metric']==metric]
        paragraphs=''.join('<p>'+esc(e['text'])+'</p>' for e in texts)
        sections.append(f'<section><h2>{esc(labels[metric])} <small>({definition["unit"]})</small></h2><p>{esc(description)}</p>'+
            chart(rows,metric,definition['unit'])+
            '<p class="legend"><span style="color:#255ec7">● DEMO-001</span> · <span style="color:#be3455">— COPS median</span> · <span style="color:#14805e">— NHANES median</span> · พื้นสี = Q25–Q75 ไม่ใช่ช่วงความเชื่อมั่น · แกน X = วันที่</p>'+
            '<div class="scroll"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in ['วัน','กรณีทดสอบ','ค่ารายวัน','Δ COPS','z COPS','Δ NHANES','z NHANES','Δ วันก่อน','QC/เหตุผล','สถานะวันก่อน'])+
            '</tr></thead><tbody>'+''.join(table)+'</tbody></table></div><details><summary>คำอธิบาย EXPECTED/template — ไม่ใช่ LLM</summary>'+paragraphs+'</details></section>')
    return '''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="is_demo" content="true"><meta name="data_origin" content="synthetic"><meta name="schema_version" content="1.0.0">
<meta name="baseline_version" content="demo-cohort-reference-v1"><title>ข้อมูลจำลอง — DEMO-001</title>
<style>body{font:16px/1.65 system-ui,sans-serif;margin:0;background:#f3f6fa;color:#16243b}header{background:#fff1c5;border-bottom:3px solid #ba6a00;padding:20px 5vw}main{max-width:1250px;margin:24px auto;padding:0 20px}section{background:white;border:1px solid #dde4ee;border-radius:12px;padding:22px;margin:24px 0}h1{margin:0}h2{font-size:21px}small{font-weight:normal}svg{width:100%;max-height:350px}table{border-collapse:collapse;font-size:13px;width:100%}th,td{border:1px solid #dce2ec;padding:8px;text-align:left;vertical-align:top}th{background:#edf2f8}.scroll{overflow:auto}.legend{font-size:13px}details{margin-top:18px}summary{cursor:pointer;font-weight:600}code{overflow-wrap:anywhere}a{color:#255ec7}</style></head><body>
<header><h1>ข้อมูลจำลอง — DEMO-001 / 10 วัน</h1><b>is_demo=true · data_origin=synthetic · baseline_version=demo-cohort-reference-v1</b><br>สำหรับตรวจรูปแบบ ไม่ใช่ข้อมูลผู้ป่วยจริงหรือ clinical baseline</header>
<main><p>ทุกระยะห่างคำนวณผ่าน baseline เดิมจาก reference train จำลอง แต่ละคนมีน้ำหนักรวมเท่ากัน DEMO-001 เป็น test จึงไม่อยู่ใน reference ไม่มีการใช้ clean parquet จริง</p>
<p>Δ กลุ่ม = ค่ารายวัน − median กลุ่ม (หน่วยเดียวกับตัวชี้วัด); z = Δ/(1.4826×MAD) ไม่มีหน่วย ไม่ใช่โอกาสเป็นโรค; Δ วันก่อน = วันนี้ − วันก่อนหน้าที่ติดกันเท่านั้น</p>
<p>null = วัดไม่ได้/เปรียบเทียบไม่ได้ตาม QC และเหตุผล ไม่ใช่ศูนย์; fraction ใช้ 0–1 ไม่ใช่ %; ช่วงรายงาน 09:00–21:00 ตัวอย่างนี้มีข้อมูลสูงสุด 6 ชั่วโมง/วัน</p>
<p><a href="analysis.json">Analysis JSON</a> · <a href="daily_features.json">Daily features</a> · <a href="model/baseline.json">Baseline จำลอง</a> · <a href="explanation_EXPECTED.json">EXPECTED/template</a> · <a href="VALIDATION_CHECKLIST.md">Checklist</a></p>'''+''.join(sections)+'''
<footer>schema_version=1.0.0 · กราฟ/ตารางมาจาก Analysis JSON · ไม่มี LLM adapter ใน repository จึงยังไม่มี explanation_MODEL.json · เปิดแบบ offline ไม่มี deploy</footer></main></body></html>'''


def run_review_demo(out):
    out=Path(out)
    if out.exists():
        raise ValueError('Demo output exists; choose a new directory')
    cfg=deepcopy(load_config())
    cfg.update(version='reference-baseline-demo-v1',baseline_version='demo-cohort-reference-v1',
               schema_version=SCHEMA_VERSION,is_demo=True,data_origin='synthetic')
    hourly,roster,provenance,expected=review_inputs(cfg)
    meta={k:provenance[k] for k in ['schema_version','is_demo','data_origin','data_kind','baseline_version']}
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    pipeline.update({k:v for k,v in meta.items() if k!='data_kind'})
    daily,qc=aggregate_daily(hourly,roster,cfg,provenance,expected_days=expected)
    train=daily[daily.split.eq('train')]
    query=daily[daily.subject_id.eq('DEMO-001')]
    write_bundle(out/'reference_bundle',train,qc[qc.split.eq('train')],roster,cfg,pipeline,
                 {**provenance,'training_bundle_only':True})
    write_bundle(out/'query_bundle',query,qc[qc.subject_id.eq('DEMO-001')],roster,cfg,pipeline,provenance)
    artifact=fit_bundle(out/'reference_bundle',out/'model')
    loaded=load_reference(out/'model/baseline.json')
    analysis=build_analysis(query,loaded)
    explanation=build_expected_explanation(analysis)
    documents={'analysis':analysis,'daily':dict(**meta,daily=clean_json(query.to_dict('records'))),
        'hourly':dict(**meta,units=dict(enmo_median='g',step_freq_walk_median='steps/s',
            tremor_frac_of_rest='fraction',valid_min='minutes'),hourly=clean_json(hourly.to_dict('records'))),
        'explanation':explanation,'baseline':artifact}
    paths={'analysis':'analysis.json','daily':'daily_features.json','hourly':'input_hourly.json',
           'explanation':'explanation_EXPECTED.json','baseline':'model/baseline.json'}
    validations=[]
    for kind,document in documents.items():
        validate_output(document,kind)
        if kind!='baseline':
            write_json(out/paths[kind],document)
        schema=deepcopy(schemas()[kind])
        schema.update({k:v for k,v in meta.items() if k!='schema_version'})
        schema['$comment']='Shared schema; this exported copy accompanies synthetic demo artifacts. Core artifact schema_version remains integer 1; public analysis/daily/explanation version is 1.0.0.'
        write_json(out/'schemas'/f'{kind}.schema.json',schema)
        validations.append(dict(file=paths[kind],schema=f'schemas/{kind}.schema.json',status='passed'))
    hourly.assign(**{k:v for k,v in meta.items() if k!='data_kind'}).to_csv(out/'input_hourly.csv',index=False)
    (out/'preview.html').write_text(preview_html(analysis,explanation,artifact),encoding='utf-8')
    checklist='''# DEMO output validation checklist

is_demo=true | data_origin=synthetic | baseline_version=demo-cohort-reference-v1 | schema_version=1.0.0

All inputs are invented hourly features, NOT raw sensor extraction. All reference statistics and distances are computed by the existing implementation. EXPECTED text is deterministic template code, not an LLM response. No real fit, provenance backfill, AWS job, endpoint or deployment.

- [ ] Confirm preview banner says ข้อมูลจำลอง, DEMO-001 has 10 days, and test subject is absent from reference entries.
- [ ] Verify units: ENMO g; cadence steps/s (not Hz of a stride, not steps/min); rest tremor fraction 0–1 (not percent).
- [ ] ENMO = weighted mean of hourly medians by n_windows; NOT a pooled daily median. Cadence weighted by n_walk. Tremor = sum(n_tremor)/sum(n_rest).
- [ ] Day 1 ENMO: six hourly values 0.038, 0.0383, 0.0386, 0.0389, 0.0392, 0.0395 with 360 windows each -> 0.03875 g. Cadence 1.45 steps/s. Tremor 42/720 = 0.05833333333333333.
- [ ] Reference TRAIN only: 6 people/18 usable days per group/indicator; each person total weight 1/6 despite 2–4 days/person. Inverse weighted CDF quantiles, MAD and scale=1.4826*MAD, no clinical interpretation.
- [ ] Distance = daily value minus cohort weighted median; standardized distance = distance/scale. Recalculate against model/baseline.json. Zero scale must retain raw-unit distance and null standardized deviation.
- [ ] Day 3 has no walking: cadence null with reasons; other indicators remain measurable. Day 4 no rest: tremor fraction null. Day 8 observed rest with zero tremor: fraction exactly 0, not null.
- [ ] Day 5 absent: all values null, no_observations. Day 6 delta is null/previous_day_missing; never bridge to Day 4.
- [ ] Day 7 only two hours: all daily values null under unchanged 360-minute/6-hour rule. Day 9 ENMO missing one hourly median: ENMO null, cadence/tremor still usable.
- [ ] Day 10 ENMO delta is null/previous_day_insufficient_data. Units of deltas equal metric units. First day delta is null/no_previous_day.
- [ ] Review per-indicator QC/coverage, reference sample counts and null reasons in analysis.json and both bundle coverage_report.json files.
- [ ] Validate all public JSON against schemas/*.schema.json with additionalProperties=false; reject wrong units, field names/types, nonfinite values and missing required fields. Schema is shared for real/demo; real provenance checks remain mandatory.
- [ ] Core model/baseline.json and bundle manifests retain internal schema_version=1 for their existing loaders. Their schema IDs/public output use 1.0.0. No field was renamed/removed. Future breaking public changes require a new schema version and advance notice.
- [ ] Compare EXPECTED evidence fields with exact Analysis JSON values; displayed text rounds to 8 significant digits. Hash links explanation to the analysis. Template wording still needs human review.
- [ ] No explanation_MODEL.json exists: no LLM adapter was found. Do not treat expected/template as a model result. Schema/number validation alone cannot prove arbitrary LLM wording correct.
- [ ] Confirm all demo CSV rows/JSON/configs carry demo metadata (internal manifests carry output_schema_version). Shared schema files are specifications, not patient records.
- [ ] Open preview.html with the network disconnected: inline SVG, no CDN/fetch/deploy. Gaps must remain gaps; shaded Q25–Q75 is IQR, not confidence interval.

Re-run into a NEW directory: python tools/reference_demo.py --review --out reports/reference_baseline/demo-output-NEW-RUN
Re-run normal CLI analysis with the same schema: python tools/reference_baseline.py compare --bundle reports/reference_baseline/demo-output/query_bundle --baseline reports/reference_baseline/demo-output/model/baseline.json --out reports/reference_baseline/demo-output/analysis_RECHECK.json
Schema definitions/semantic validation: src/reference/output.py. JSON Schema locks shape, unit and nullable fields; extra validators check arithmetic and template evidence. Production real analysis uses this exact build_analysis()/validate_output() path after load_reference(); real fit still requires historical clean evidence.
'''
    checklist=checklist.replace('reports/reference_baseline/demo-output/',out.as_posix()+'/')
    (out/'VALIDATION_CHECKLIST.md').write_text(checklist,encoding='utf-8')
    validate_explanation(explanation,analysis)
    write_json(out/'validation.json',dict(**meta,json_schema_results=validations,
        arithmetic_validation='passed',expected_evidence_validation='passed',
        llm_adapter_status='not_available_not_run',llm_model_output_created=False,
        scenarios={str(i):s for i,s in enumerate(SCENARIOS)},code_version=code_version()))
    files={p.relative_to(out).as_posix():sha256(p) for p in sorted(out.rglob('*')) if p.is_file()}
    write_json(out/'manifest.json',dict(**meta,files=files,
        input_level='invented_hourly_features_not_raw_signals',computed='existing daily aggregation, train reference fit, comparison, consecutive-day delta',
        expected_explanation='deterministic template, not LLM',model_explanation='not_available_not_run'))
    print(f'SYNTHETIC DEMO ONLY: DEMO-001 / 10 days / {len(analysis["analysis"])} metric-day rows; {out/"preview.html"}')

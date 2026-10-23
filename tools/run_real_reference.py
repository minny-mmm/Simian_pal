"""Resume a real-data run using existing clean/extract/daily/fit functions.

No ingestion replacement, new split, AWS job, endpoint, or synthetic fallback.
Test signal/QC contents are not read by any stage. All writes use a new run namespace.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import ctypes
from datetime import datetime, timezone
import gc
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import traceback

import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src import storage
from src.clean import run as clean
from src.ingestion.cops_reader import FILE_RE
from src.reference.common import fingerprint, load_config, load_roster, sha256, write_json
from src.reference.provenance import CLEAN_SOURCE_PATHS, validate_clean_record


def now():
    return datetime.now(timezone.utc).isoformat()


def memory_headroom():
    if sys.platform=='win32':
        class M(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(k,ctypes.c_ulonglong) for k in ['total','avail','tp','ap','tv','av','ae']]
        m=M();m.length=ctypes.sizeof(M)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return dict(available_ram=m.avail,available_commit=m.ap)
    import os
    free=os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    return dict(available_ram=free,available_commit=free)


def source_version():
    return dict(git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        files={p:sha256(ROOT/p) for p in CLEAN_SOURCE_PATHS})


def dependency_versions():
    return dict(python=platform.python_version(),platform=platform.platform(),
        packages={p:importlib.metadata.version(p) for p in ['numpy','pandas','scipy','pyarrow','PyYAML','s3fs']})


def paths(run_id):
    if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise ValueError('Run ID must be a simple new namespace')
    return dict(report=ROOT/'reports/reference_baseline'/run_id,raw=ROOT/'data/raw/reference-runs'/run_id,
        clean=ROOT/'data/clean/reference-runs'/run_id,hourly=ROOT/'data/features/reference-v1'/run_id,
        daily=ROOT/'data/daily/reference-v1'/run_id,model=ROOT/'data/models/reference-v1'/run_id)


def initialize(run_id):
    p=paths(run_id); report=p['report']; report.mkdir(parents=True,exist_ok=True)
    config_path=report/'pipeline_config.yaml'
    if config_path.exists():
        c=yaml.safe_load(config_path.read_text(encoding='utf-8'))
        m=json.loads((report/'run_manifest.json').read_text(encoding='utf-8'))
        if sha256(config_path)!=m['pipeline_config_sha256'] or any(sha256(ROOT/k)!=v for k,v in m['split_hashes'].items()):
            raise ValueError('Run config/splits changed; start a new run, do not mix preprocessing')
        if sha256(ROOT/'config/config.yaml')!=m['original_pipeline_config_sha256']:
            raise ValueError('Agreed pipeline config changed after run initialization')
        if source_version()!=m['source_version']:
            raise ValueError('Clean source/code revision changed; use a new run rather than mix preprocessing versions')
        expected=load_config();expected['baseline_version']=f'real-cohort-reference-{run_id}'
        if load_config(report/'reference_config.yaml')!=expected:
            raise ValueError('Reference config changed; do not tune coverage or fit policy in an existing run')
        return p,c
    c=deepcopy(clean.load_config())
    originals={ds:v['raw_dir'] for ds,v in c['datasets'].items()}
    for ds in ['cops','nhanes']:
        c['datasets'][ds]['raw_dir']=(p['raw']/ds).relative_to(ROOT).as_posix()
    c['clean']['out_dir']=p['clean'].relative_to(ROOT).as_posix()
    c['clean']['qc_summary']=(report/'qc_summary.csv').relative_to(ROOT).as_posix()
    config_path.write_text(yaml.safe_dump(c,allow_unicode=True),encoding='utf-8')
    cfg=load_config();cfg['baseline_version']=f'real-cohort-reference-{run_id}'
    (report/'reference_config.yaml').write_text(yaml.safe_dump(cfg,allow_unicode=True),encoding='utf-8')
    splits={c['clean']['subjects'][k]:sha256(ROOT/c['clean']['subjects'][k]) for k in ['cops_splits','nhanes_selected','cops_summary']}
    write_json(report/'run_manifest.json',dict(schema_version=1,data_kind='real',is_demo=False,data_origin='real',
        run_id=run_id,created_utc=now(),original_pipeline_config_sha256=sha256(ROOT/'config/config.yaml'),
        pipeline_config_sha256=sha256(config_path),original_raw_dirs=originals,split_hashes=splits,
        source_version=source_version(),dependencies=dependency_versions(),test_policy='inventory names only; no test signal/QC content opened',
        policy='Only storage routing and baseline run identifier changed; clean/feature/QC thresholds and splits retained.'))
    return p,c


def remote_files(c):
    storage.configure(c['storage']);uri=storage.s3_root(c)
    fs=storage.fs_of(uri)
    root=uri.split('://',1)[1].rstrip('/')
    return fs,root


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def inspect(run_id):
    p,c=initialize(run_id)
    if (p['report']/'inventory.json').exists():
        raise ValueError('Inventory snapshot already exists; use a new run ID, never overwrite an active run inventory')
    f,root=remote_files(c);inventory={}; searched=[]
    for rel in ['data/clean','data/features','data/raw/cops','data/raw/nhanes','metadata','config','reports','logs','manifests']:
        try: items=f.find(root+'/'+rel,detail=True)
        except FileNotFoundError: items={}
        searched.append(dict(prefix='s3://'+root+'/'+rel,n_objects=len(items)))
        for key,v in items.items():
            if v.get('type')=='file' and v.get('size',0):
                inventory[key]=dict(size=v['size'],etag=v.get('ETag'),modified=str(v.get('LastModified','')))
    write_json(p['report']/'inventory.json',dict(checked_utc=now(),searched=searched,files=inventory))
    subjects=clean.load_subjects(c)
    rows=[]; evidence=[]
    for s in subjects:
        sid,ds,split=s['subject_id'],s['dataset'],s['split']
        raw=select_raw(inventory,root,s)
        qkey=f'{root}/data/clean/{ds}/{sid}.qc.json'
        features=[k for k in inventory if '/data/features/' in k and Path(k).name==sid+'.csv']
        rows.append(dict(**s,raw_files=len(raw),raw_bytes=sum(inventory[k]['size'] for k in raw),
            old_qc_present=qkey in inventory,old_features=len(features),provenance='unverified',test_contents_opened=False))
        if split=='test':
            continue
        # Read train/validation evidence only; no inference from upload paths or timestamps.
        candidates=[k for k in inventory if (sid+'/' in k or Path(k).name.startswith(sid+'.'))
            and (k.endswith('.json') or k.endswith('.log')) and not k.endswith('.qc.json')]
        for key in candidates:
            raw_bytes=f.cat(key)
            dest=p['report']/'historical_evidence'/ds/sid/Path(key).name
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw_bytes)
            evidence.append(dict(uri='s3://'+key,sha256=sha256(dest),local=str(dest),
                note='Evidence candidate, not automatically accepted as clean lineage'))
    pd.DataFrame(rows).to_csv(p['report']/'availability.csv',index=False)
    write_json(p['report']/'historical_search.json',dict(checked_utc=now(),searched=searched,candidates=evidence,
        git_clean_history=subprocess.check_output(['git','log','--all','--oneline','--','src/clean/run.py','config/config.yaml'],cwd=ROOT,text=True),
        conclusion='Review candidates. Raw _source.json proves acquisition only, not clean/feature config+code+output linkage.',
        missing=['original clean config+source revision linked to each parquet/QC','extractor config/source linked to hourly CSV and clean outputs']))
    print(pd.DataFrame(rows).groupby(['dataset','split']).agg(people=('subject_id','size'),raw_available=('raw_files',lambda x:int((x>0).sum()))).to_string(),flush=True)
    print('Historical evidence candidates:',len(evidence),flush=True)
    return rows


def select_raw(inventory,root,subject):
    ds,sid=subject['dataset'],subject['subject_id']
    if ds=='nhanes':
        key=f'{root}/data/raw/nhanes/2013-2014/pax80_raw/{sid.split("-")[-1]}.tar.bz2'
        return [key] if key in inventory else []
    prefix=f'{root}/data/raw/cops/{sid}/'
    keys=[k for k in inventory if k.startswith(prefix)]
    sensor=[]
    for k in keys:
        m=FILE_RE.match(Path(k).name)
        if m and m[5].lower()==subject['wrist']:
            if k.endswith('.zip') and k[:-4]+'.csv' in inventory: continue
            sensor.append(k)
    diary=prefix+sid+'_symptomdiary.csv'
    if not sensor or diary not in inventory:
        return []  # incomplete COPS diary must not silently turn all hours into outpatient
    return sorted(sensor)+[diary]


def acquire(run_id,subject):
    p,c=initialize(run_id)
    inventory=read_json(p['report']/'inventory.json')['files'];fs,root=remote_files(c)
    keys=select_raw(inventory,root,subject)
    if not keys: raise FileNotFoundError('raw_or_required_diary_missing_on_s3')
    ds,sid=subject['dataset'],subject['subject_id']
    inputs=[]
    def download(key):
        if ds=='cops': relative=key.split('/data/raw/cops/',1)[1]
        else: relative='pax80_raw/'+Path(key).name
        local=p['raw']/ds/relative
        # The cache is private to this run. Reuse only with its own recorded SHA256.
        proof=local.with_name(local.name+'.download.json')
        if not (local.exists() and proof.exists() and read_json(proof).get('sha256')==sha256(local)):
            if local.exists(): raise ValueError(f'Unverified existing run cache: {local}')
            storage.download('s3://'+key,local)
            info=fs.info(key)
            if info.get('ETag')!=inventory[key]['etag']: raise ValueError('Raw object changed since inventory; use a new run')
            write_json(proof,dict(uri='s3://'+key,sha256=sha256(local),size=local.stat().st_size,
                etag=info.get('ETag'),version_id=info.get('VersionId'),downloaded_utc=now(),run_id=run_id))
        return dict(path=local.relative_to(ROOT).as_posix(),**read_json(proof))
    with ThreadPoolExecutor(max_workers=4) as pool:
        inputs=list(pool.map(download,keys))
    for rel in c['clean']['subjects'].values():
        inputs.append(dict(path=rel,sha256=sha256(ROOT/rel),kind='split_or_cohort_metadata'))
    write_json(p['report']/'inputs'/f'{sid}.json',dict(run_id=run_id,subject=subject,inputs=inputs,captured_utc=now()))
    return inputs


def resource_check(c,subject,inputs):
    if subject['dataset']=='cops':
        matches=[FILE_RE.match(Path(r['path']).name) for r in inputs]
        duration=max((int(m[2])*24+int(m[3])+1 for m in matches if m),default=0)*3600
    else: duration=9*86400  # conservative full recording allocation; exact duration is checked by cleaner
    samples=duration*c['clean']['resample']['fs_out']
    need=max(3*2**30,samples*160)  # xyz/flags/masks + temporary arrays, engineering resource guard only
    memory=memory_headroom()
    if memory['available_commit'] < need+2**30:
        raise MemoryError(f'clean memory guard: need {need/2**30:.2f} GiB + 1 GiB headroom; available commit {memory["available_commit"]/2**30:.2f} GiB')
    return dict(**memory,estimated_peak_bytes=need)


def clean_one(run_id,sid):
    for stream in [sys.stdout,sys.stderr]:
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    p,c=initialize(run_id)
    subjects=[s for s in clean.load_subjects(c) if s['subject_id']==sid and s['split'] in ['train','valid']]
    if len(subjects)!=1: raise ValueError('Only authoritative train/valid subjects are allowed; test is locked')
    subject=subjects[0];ds=subject['dataset'];dest=p['clean']/ds
    pq=dest/f'{sid}.parquet';qcpath=dest/f'{sid}.qc.json';manifest=dest/f'{sid}.clean.manifest.json'
    if manifest.exists():
        m=read_json(manifest)
        if m.get('status')!='completed': raise ValueError('Prior failed run; inspect evidence and use a new run ID')
        if m.get('parquet_sha256'):
            validate_clean_record(m,c['clean'],ds,sid,subject['split'])
            if sha256(pq)!=m['parquet_sha256']:raise ValueError('Completed parquet changed')
        if sha256(qcpath)!=m['qc_sha256']:raise ValueError('Completed QC changed')
        print(sid,'verified completed output reused',flush=True);return
    if pq.exists() or qcpath.exists(): raise ValueError('Unmanifested output exists; never backfill it')
    previous_start=dest/f'{sid}.started.json'
    if previous_start.exists():
        failure=p['report']/f'blocked_clean-one_{sid}.json'
        if not failure.exists():
            raise ValueError('A worker already started in this namespace; inspect its log/process before starting a new run')
        progress=p['report']/f'{subject["split"]}_progress.json'
        finished=progress.exists() and any(r['subject_id']==sid and r['status']=='failed' for r in read_json(progress)['rows'])
        if not finished:
            raise ValueError('Retry requires controller evidence that the previous worker exited; do not retry a live worker')
        # Retry only a recorded failed attempt that produced no outputs. Preserve original evidence.
        archive=dest/'attempts'/sid
        archive.mkdir(parents=True,exist_ok=True)
        stamp=read_json(previous_start)['started_utc'].replace(':','').replace('+','_')
        archived=archive/f'{stamp}.started.json'
        if archived.exists() or archived.with_suffix('.failure.json').exists():raise ValueError('Attempt archive already exists')
        if not previous_start.resolve().is_relative_to(p['clean'].resolve()) or not archived.resolve().is_relative_to(p['clean'].resolve()):
            raise ValueError('Unsafe attempt archive path')
        previous_start.rename(archived)
        failure.rename(archived.with_suffix('.failure.json'))
    inputpath=p['report']/'inputs'/f'{sid}.json'
    inputs=read_json(inputpath)['inputs'] if inputpath.exists() else acquire(run_id,subject)
    for row in inputs:
        if sha256(ROOT/row['path'])!=row['sha256']:raise ValueError('Input changed before clean')
    resources=resource_check(c,subject,inputs)
    lock_path=p['report']/'noise_lock.json'
    if ds=='nhanes' or subject['split']=='valid':
        if not lock_path.exists():raise ValueError('Train harmonization lock required before NHANES or validation')
        lock=read_json(lock_path)
        if lock['pipeline_config_sha256']!=sha256(p['report']/'pipeline_config.yaml'):raise ValueError('Noise lock config mismatch')
        target=np.asarray(lock['target_axes_mg']) if ds=='nhanes' else None
    else:target=None
    before=source_version();start=now();dest.mkdir(parents=True,exist_ok=True)
    started=dict(schema_version=1,kind='clean_run_started',run_id=run_id,**subject,started_utc=start,
        code_version=before,resolved_config=c,dependencies=dependency_versions(),inputs=inputs,resources=resources)
    write_json(dest/f'{sid}.started.json',started)
    reader=clean.make_reader(c,ds)
    result,qc=clean.clean_subject(c,subject,reader,target,log=lambda s:print(s,flush=True))
    if before!=source_version():raise ValueError('Clean source changed during execution; outputs must not be certified')
    qc['noise_target_source']='locked_agreed_config' if target is not None else None
    if result is not None:clean.write_parquet(pq,result)
    del result;gc.collect()
    write_json(qcpath,qc)
    record=dict(schema_version=1,kind='clean_run_evidence',run_id=run_id,recorded_at_utc=now(),
        evidence_uri=(dest/f'{sid}.started.json').as_uri(),**subject,status='completed',started_utc=start,
        resolved_clean_config=c['clean'],resolved_config=c,code_version=before,dependencies=dependency_versions(),
        inputs=inputs,pipeline_config_sha256=sha256(p['report']/'pipeline_config.yaml'),
        runtime_noise_target_axes_mg=None if target is None else target.tolist(),
        parquet_sha256=sha256(pq) if pq.exists() else None,qc_sha256=sha256(qcpath),
        included=qc.get('included',False),qc_status=qc['status'])
    write_json(manifest,record)
    print(sid,'completed',qc['status'],'included',qc.get('included'),'valid_days',qc.get('valid_days'),flush=True)


def lock_noise(run_id):
    p,c=initialize(run_id)
    inventory=read_json(p['report']/'inventory.json')['files'];_,root=remote_files(c)
    planned=[s for s in clean.load_subjects(c) if s['dataset']=='cops' and s['split']=='train' and select_raw(inventory,root,s)]
    missing=[s['subject_id'] for s in planned if not (p['clean']/'cops'/f'{s["subject_id"]}.clean.manifest.json').exists()]
    configured=c['clean']['harmonize']['add_noise']['target_mg']
    if configured=='auto':
        raise ValueError('Automatic target requires completed train estimates and a versioned runtime signature adapter; do not silently substitute a fixed value')
    axes=[];sources=[]
    for s in planned:
        if s['subject_id'] in missing:continue
        qpath=p['clean']/'cops'/f'{s["subject_id"]}.qc.json';q=read_json(qpath)
        record=read_json(p['clean']/'cops'/f'{s["subject_id"]}.clean.manifest.json')
        if record.get('status')!='completed':raise ValueError('Train clean run has not completed')
        if record.get('parquet_sha256'):
            validate_clean_record(record,c['clean'],'cops',s['subject_id'],'train')
            if sha256(p['clean']/'cops'/f'{s["subject_id"]}.parquet')!=record['parquet_sha256']:
                raise ValueError('Train clean output checksum changed')
        if sha256(qpath)!=record['qc_sha256']:raise ValueError('Train QC checksum changed')
        a=q.get('noise_floor_after_axes_mg')
        if a is not None and np.isfinite(a).all():axes.append(a);sources.append(dict(subject_id=s['subject_id'],qc_sha256=sha256(qpath)))
    computed=np.median(axes,axis=0).tolist() if axes else None
    target=np.broadcast_to(np.asarray(configured,float),(3,)).tolist()
    document=dict(schema_version=1,created_utc=now(),run_id=run_id,fit_split='train',
        mode='fixed_agreed_config_not_refitted',target_axes_mg=target,
        pipeline_config_sha256=sha256(p['report']/'pipeline_config.yaml'),
        note='Existing agreed numeric target frozen, not learned in this run. Separate diagnostics compare current train only. Validation/test never used.')
    write_json(p['report']/'train_noise_diagnostics.json',dict(current_train_median_axes_mg=computed,
        difference_axes_mg=(np.asarray(target)-computed).tolist() if computed is not None else None,
        sources=sources,pending_train_subjects=missing,test_or_validation_used=False,
        configured_target_retained=True))
    path=p['report']/'noise_lock.json'
    if path.exists():
        old=read_json(path)
        if old['target_axes_mg']!=target or old['pipeline_config_sha256']!=document['pipeline_config_sha256']:raise ValueError('Frozen noise dependency changed; start new run')
    else:write_json(path,document)
    print('Harmonization locked:',target,'current train median',computed,flush=True)


def run_subjects(run_id,split,ids=None,workers=1):
    p,c=initialize(run_id);inventory=read_json(p['report']/'inventory.json')['files'];_,root=remote_files(c)
    subjects=[s for s in clean.load_subjects(c) if s['split']==split]
    if split not in ['train','valid']:raise ValueError('Test evaluation is locked')
    if ids:
        if not set(ids)<=set(s['subject_id'] for s in subjects):raise ValueError('IDs do not belong to requested split')
        subjects=[s for s in subjects if s['subject_id'] in ids]
    lock_noise(run_id)
    progress_path=p['report']/f'{split}_progress.json'
    previous=read_json(progress_path)['rows'] if progress_path.exists() else []
    statuses={row['subject_id']:row for row in previous}
    # Interleave cohorts without using measurements to select or reorder participants.
    groups=[[s for s in subjects if s['dataset']==ds] for ds in ['cops','nhanes']]
    ordered=[]
    for i in range(max(map(len,groups),default=0)):
        ordered.extend(g[i] for g in groups if i<len(g))
    stop_network=threading.Event();network_lock=threading.Lock();network_failures=0
    def process(s):
            nonlocal network_failures
            sid=s['subject_id']
            if not select_raw(inventory,root,s):
                return dict(**s,status='blocked_raw_missing')
            if stop_network.is_set():return dict(**s,status='deferred_network_outage')
            log=p['report']/'logs'/f'{sid}.log';log.parent.mkdir(exist_ok=True)
            print(now(),sid,'clean worker starting',flush=True)
            with log.open('a',encoding='utf-8') as fh:
                result=subprocess.run([sys.executable,'-u','-X','utf8',str(Path(__file__).resolve()),'--run-id',run_id,'--stage','clean-one','--ids',sid],stdout=fh,stderr=subprocess.STDOUT)
            status='completed' if result.returncode==0 else 'failed'
            failure=p['report']/f'blocked_clean-one_{sid}.json'
            with network_lock:
                if status=='failed' and failure.exists() and read_json(failure).get('exception_type') in ['EndpointConnectionError','ConnectionClosedError','ConnectTimeoutError','ReadTimeoutError','AioReadTimeoutError','FSTimeoutError']:
                    network_failures+=1
                    if network_failures>=3:stop_network.set()
                elif status=='completed':network_failures=0
            print(now(),sid,status,flush=True)
            return dict(**s,status=status,returncode=result.returncode,log=str(log))
    from concurrent.futures import as_completed
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(process,s) for s in ordered]
        for future in as_completed(futures):
            row=future.result();statuses[row['subject_id']]=row
            write_json(progress_path,dict(updated_utc=now(),rows=list(statuses.values())))
    write_json(progress_path,dict(updated_utc=now(),rows=list(statuses.values())))
    requested=[statuses[s['subject_id']] for s in subjects]
    failed=[r['subject_id'] for r in requested if r['status'] in ['failed','deferred_network_outage']]
    if failed:raise RuntimeError(f'Processing incomplete for {len(failed)} {split} subjects; inspect progress/logs and resume. No automatic fit/evaluation from this incomplete batch.')
    return requested


def features_and_bundle(run_id,split,cfg=None,bundle_out=None,exclusions_out=None):
    from tools.extract_reference_features import extract
    from src.reference.io import create_bundle
    p,c=initialize(run_id);cfg=cfg or load_config(p['report']/'reference_config.yaml');roster=load_roster(pipeline=c)
    excluded=[];complete=[]
    for s in clean.load_subjects(c):
        if s['split']!=split:continue
        sid,ds=s['subject_id'],s['dataset'];qpath=p['clean']/ds/f'{sid}.qc.json'
        if not qpath.exists():excluded.append(dict(**s,reason='no_completed_clean'));continue
        q=read_json(qpath)
        if q.get('status')!='ok' or not q.get('included'):
            excluded.append(dict(**s,reason='clean_qc_excluded',valid_days=q.get('valid_days')));continue
        hourly=p['hourly']/split/ds/f'{sid}.csv'
        if not hourly.exists():
            extract(ds,sid,p['clean']/ds/f'{sid}.parquet',qpath,p['hourly']/split,roster,cfg,c)
        complete.append(sid)
    write_json(exclusions_out or p['report']/f'{split}_exclusions.json',dict(data_kind='real',run_id=run_id,excluded=excluded))
    if not complete:raise ValueError('No QC-included real subjects; no baseline or analysis created')
    out=bundle_out or p['daily']/split
    if not out.exists():
        create_bundle(p['hourly']/split,out,roster,cfg,c,
            {name:sha256(ROOT/name) for name in c['clean']['subjects'].values()})
    else:
        from src.reference.io import load_bundle
        _,_,_,provenance=load_bundle(out)
        recorded={(s['dataset'],s['subject_id']):s['hourly_sha256'] for s in provenance['sources']}
        actual={(f.parent.name,f.stem):sha256(f) for f in (p['hourly']/split).rglob('*.csv')}
        if recorded!=actual:raise ValueError('Daily input set changed after bundle creation; use a new version, do not overwrite baseline inputs')
    return out


def fit(run_id):
    from tools.reference_baseline import fit_bundle
    from src.reference.model import load_reference
    p,_=initialize(run_id);bundle=features_and_bundle(run_id,'train')
    artifact=load_reference(p['model']/'baseline.json') if (p['model']/'baseline.json').exists() else fit_bundle(bundle,p['model'])
    write_json(p['report']/'training_result.json',dict(data_kind='real',is_demo=False,run_id=run_id,
        fit_split='train',baseline_artifact=str(p['model']/'baseline.json'),baseline_sha256=sha256(p['model']/'baseline.json'),
        artifact_id=artifact['artifact_id'],references={ds:{k:v['summary'] for k,v in metrics.items()} for ds,metrics in artifact['references'].items()},
        exclusions=str(p['report']/'train_exclusions.json'),test_evaluated=False))
    test=[s for s in clean.load_subjects(initialize(run_id)[1]) if s['split']=='test']
    write_json(p['report']/'test_plan_NOT_EXECUTED.json',dict(status='prepared_not_opened',subjects=test,
        locked_baseline_sha256=sha256(p['model']/'baseline.json'),locked_pipeline_config_sha256=sha256(p['report']/'pipeline_config.yaml'),
        locked_reference_config_sha256=sha256(p['report']/'reference_config.yaml'),
        requires='Explicit future test execution after validation review; clean/extract under identical locked config then compare only, never fit.',
        test_signal_or_qc_contents_opened=False))
    print('REAL baseline',p['model']/'baseline.json',flush=True)
    return artifact


def evaluate(run_id,workers=1,checkpoint_name=None,ids=None):
    from src.reference.io import load_bundle
    from src.reference.model import load_reference
    from src.reference.output import build_analysis
    p,_=initialize(run_id)
    if checkpoint_name:paths(checkpoint_name)
    model=(p['model']/'checkpoints'/checkpoint_name if checkpoint_name else p['model'])/'baseline.json'
    report=p['report']/'checkpoints'/checkpoint_name if checkpoint_name else p['report']
    if not model.exists():raise ValueError('Fit train and lock config before validation')
    artifact=load_reference(model)
    before=sha256(model)
    evaluated_subjects=run_subjects(run_id,'valid',ids=ids,workers=workers)
    bundle=features_and_bundle(run_id,'valid',cfg=artifact['config'],
        bundle_out=p['daily']/'checkpoints'/checkpoint_name/'valid' if checkpoint_name else None,
        exclusions_out=report/'valid_exclusions.json')
    daily,_,_,_=load_bundle(bundle)
    doc=build_analysis(daily,artifact)
    write_json(report/'analysis_VALIDATION_REAL.json',doc)
    if before!=sha256(model):raise ValueError('Baseline changed during validation')
    write_json(report/'validation_report.json',dict(data_kind='real',baseline_sha256=before,
        baseline_unchanged=True,fit_split='train',evaluation_split='valid',test_evaluated=False,
        requested_subjects=evaluated_subjects,scope='explicit_validation_subset' if ids else 'all_available_original_validation',
        rows=len(doc['analysis']),status_counts=pd.Series([r['status'] for r in doc['analysis']]).value_counts().to_dict(),
        daily_coverage=[dict(dataset=ds,metric=metric,n_people=int(g.subject_id.nunique()),n_days=len(g),
            n_people_usable=int(g.loc[g.status.eq('ok'),'subject_id'].nunique()),n_days_usable=int(g.status.eq('ok').sum()))
            for (ds,metric),g in daily.groupby(['dataset','metric'])],
        comparison_status_counts=[dict(reference=ref,metric=metric,status_counts=pd.Series([
            r['reference_comparisons'][ref]['status'] for r in doc['analysis'] if r['metric']==metric]).value_counts().to_dict())
            for ref in ['cops','nhanes'] for metric in artifact['metrics']],
        note='Coverage/cohort-distance evaluation only; no diagnostic accuracy or clinical severity claim.'))


def checkpoint(run_id,name):
    """Freeze all completed, QC-included TRAIN subjects; never claim full-run completion."""
    from tools.extract_reference_features import extract
    from tools.reference_baseline import fit_bundle
    from src.reference.io import create_bundle
    from src.reference.output import build_analysis
    paths(name)  # validate the checkpoint namespace, too
    p,c=initialize(run_id)
    report=p['report']/'checkpoints'/name
    if report.exists():raise ValueError('Checkpoint exists; use a new checkpoint name')
    cfg=load_config(p['report']/'reference_config.yaml')
    cfg['baseline_version']+='-'+name
    roster=load_roster(pipeline=c)
    selected=[];excluded=[]
    for s in clean.load_subjects(c):
        if s['split']!='train':continue
        sid,ds=s['subject_id'],s['dataset']
        manifest=p['clean']/ds/f'{sid}.clean.manifest.json'
        if not manifest.exists():
            excluded.append(dict(**s,reason='not_completed_at_checkpoint'));continue
        m=read_json(manifest);qpath=p['clean']/ds/f'{sid}.qc.json';q=read_json(qpath)
        if m.get('status')!='completed' or sha256(qpath)!=m['qc_sha256']:
            raise ValueError('Checkpoint requires completed, unchanged clean evidence')
        if not q.get('included'):
            excluded.append(dict(**s,reason='clean_qc_excluded',valid_days=q.get('valid_days')));continue
        validate_clean_record(m,c['clean'],ds,sid,'train')
        if sha256(p['clean']/ds/f'{sid}.parquet')!=m['parquet_sha256']:
            raise ValueError('Checkpoint clean checksum changed')
        selected.append(dict(**s,clean_manifest_sha256=sha256(manifest)))
    report.mkdir(parents=True)
    snapshot=dict(schema_version=1,data_kind='real',is_demo=False,run_id=run_id,checkpoint=name,
        created_utc=now(),status='started',selection='ALL completed QC-included original train subjects at snapshot time; no feature-value selection',
        selected=selected,excluded=excluded,full_train_complete=False,validation_evaluated=False,test_evaluated=False)
    write_json(report/'checkpoint_manifest.json',snapshot)
    hourly=p['hourly']/'checkpoints'/name/'train'
    for s in selected:
        sid,ds=s['subject_id'],s['dataset'];source=p['hourly']/'train'/ds/f'{sid}.csv'
        if not source.exists():
            extract(ds,sid,p['clean']/ds/f'{sid}.parquet',p['clean']/ds/f'{sid}.qc.json',p['hourly']/'train',roster,cfg,c)
        target=hourly/ds/source.name;target.parent.mkdir(parents=True,exist_ok=True)
        for src,dst in [(source,target),(source.with_suffix('.manifest.json'),target.with_suffix('.manifest.json'))]:
            shutil.copyfile(src,dst)
            if sha256(src)!=sha256(dst):raise ValueError('Checkpoint copy checksum mismatch')
    bundle=p['daily']/'checkpoints'/name/'train'
    daily,_=create_bundle(hourly,bundle,roster,cfg,c,{k:sha256(ROOT/k) for k in c['clean']['subjects'].values()})
    model=p['model']/'checkpoints'/name
    artifact=fit_bundle(bundle,model)
    write_json(report/'analysis_TRAIN_REAL.json',build_analysis(daily,artifact))
    snapshot.update(status='completed',completed_utc=now(),baseline_artifact=str(model/'baseline.json'),
        baseline_sha256=sha256(model/'baseline.json'),artifact_id=artifact['artifact_id'],
        daily_bundle=str(bundle),coverage_report=str(bundle/'coverage_report.json'),
        analysis=str(report/'analysis_TRAIN_REAL.json'),analysis_split='train',
        train_comparison='All days of the scored person excluded from their own cohort reference; not validation.',
        references={ds:{k:v['summary'] for k,v in metrics.items()} for ds,metrics in artifact['references'].items()})
    write_json(report/'checkpoint_manifest.json',snapshot)
    print('REAL PILOT CHECKPOINT',report/'checkpoint_manifest.json',flush=True)
    return snapshot


def status(run_id):
    p,c=initialize(run_id);rows=[]
    for path in sorted(p['clean'].rglob('*.clean.manifest.json')):
        m=read_json(path)
        if m.get('status')!='completed':continue
        q=read_json(path.with_name(m['subject_id']+'.qc.json'))
        if sha256(path.with_name(m['subject_id']+'.qc.json'))!=m['qc_sha256']:
            raise ValueError('Completed QC changed')
        rows.append(dict(dataset=m['dataset'],split=m['split'],subject_id=m['subject_id'],
            included=q.get('included',False),valid_days=q.get('valid_days',0),hours_valid=q.get('hours_valid',0)))
    frame=pd.DataFrame(rows)
    groups=[]
    if len(frame):
        for (ds,split),g in frame.groupby(['dataset','split']):
            good=g[g.included]
            groups.append(dict(dataset=ds,split=split,n_completed=len(g),n_qc_included=len(good),
                qc_valid_days=int(good.valid_days.sum()),qc_valid_hours=float(good.hours_valid.sum())))
    model=p['model']/'baseline.json';analysis=p['report']/'analysis_VALIDATION_REAL.json'
    checkpoints=[]
    for f in sorted((p['report']/'checkpoints').glob('*/checkpoint_manifest.json')):
        item=read_json(f);validation=f.parent/'validation_report.json'
        checkpoints.append({**{k:item.get(k) for k in ['checkpoint','status','baseline_artifact','baseline_sha256','analysis','full_train_complete']},
            'validation_report':str(validation) if validation.exists() else None})
    doc=dict(recorded_utc=now(),schema_version=1,data_kind='real',is_demo=False,run_id=run_id,
        completed_clean=groups,completed_subjects=rows,
        note='Clean QC totals are NOT per-indicator daily coverage. Only completed new-run manifests counted.',
        baseline_artifact=str(model) if model.exists() else None,
        pilot_checkpoints=checkpoints,
        validation_analysis=str(analysis) if analysis.exists() else None,test_signal_contents_opened=False,
        memory={k:round(v/2**30,2) for k,v in memory_headroom().items()})
    write_json(p['report']/'STATUS_REAL.json',doc)
    print(json.dumps({k:v for k,v in doc.items() if k!='completed_subjects'},indent=2),flush=True)
    return doc


def main():
    for stream in [sys.stdout,sys.stderr]:
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8']='1'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--stage',choices=['inspect','train','train-validation','clean-one','lock','fit','validation','checkpoint','status'],required=True)
    parser.add_argument('--checkpoint-name',help='New immutable TRAIN snapshot name, or existing checkpoint to evaluate on validation')
    parser.add_argument('--ids',nargs='+')
    parser.add_argument('--workers',type=int,choices=[1,2],default=1,help='Sequential by default; at most two memory-guarded clean workers')
    a=parser.parse_args();p,_=initialize(a.run_id)
    try:
        if a.stage=='inspect':inspect(a.run_id)
        elif a.stage=='clean-one':
            if not a.ids or len(a.ids)!=1:raise ValueError('clean-one requires one subject ID')
            clean_one(a.run_id,a.ids[0])
        elif a.stage in ['train','train-validation']:
            run_subjects(a.run_id,'train',a.ids,a.workers)
            lock_noise(a.run_id)
            fit(a.run_id)
            if a.stage=='train-validation':
                evaluate(a.run_id,a.workers)
                status(a.run_id)
        elif a.stage=='lock':lock_noise(a.run_id)
        elif a.stage=='fit':fit(a.run_id)
        elif a.stage=='validation':evaluate(a.run_id,a.workers,a.checkpoint_name,a.ids)
        elif a.stage=='checkpoint':checkpoint(a.run_id,a.checkpoint_name or 'checkpoint-01')
        elif a.stage=='status':status(a.run_id)
    except Exception as exc:
        suffix='_'+a.ids[0] if a.stage=='clean-one' and a.ids else ''
        write_json(p['report']/f'blocked_{a.stage}{suffix}.json',dict(recorded_utc=now(),stage=a.stage,
            data_kind='real',reason=str(exc),exception_type=type(exc).__name__,real_baseline_exists=any(p['model'].rglob('baseline.json')),
            checkpoint_name=a.checkpoint_name))
        raise


if __name__=='__main__':
    main()

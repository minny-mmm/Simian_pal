"""Freeze evidence for an existing pilot; no training submission or test reads."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_reference_aws import context, check_snapshot, read, upload_file
from src.reference.common import sha256, write_json
from src.reference.io import load_bundle
from botocore.exceptions import ClientError

EXPECTED = {'COPS-7', 'NHANES-81262'}
COMMIT = '7989ba8dba461122b3aa750c8ea7501f1382a5d9'


def remote_hash(s3, uri):
    bucket, key = uri[5:].split('/', 1)
    response = s3.get_object(Bucket=bucket, Key=key)
    digest = hashlib.sha256()
    try:
        for chunk in response['Body'].iter_chunks(chunk_size=1024 * 1024):
            digest.update(chunk)
    finally:
        response['Body'].close()
    return dict(uri=uri, sha256=digest.hexdigest(), bytes=response['ContentLength'],
                last_modified_utc=response['LastModified'].isoformat(),
                version_id=response.get('VersionId'))


def verify(cfg, p, pipeline, session):
    manifest = check_snapshot(p)
    if manifest['run_id'] != cfg['run_id'] or manifest['intended_job_name'] != cfg['job_name']:
        raise ValueError('Wrong run/job identity')
    if manifest['code_revision']['git_commit'] != COMMIT:
        raise ValueError('Wrong snapshot source commit')
    if ROOT.resolve() != Path('C:/working/a').resolve():
        raise ValueError('Unexpected workspace origin')
    root = pipeline['storage']['s3_root'].rstrip('/')
    targets = [(p['report']/'input_manifest.json', root+'/reports/reference_baseline/'+cfg['run_id']+'/input_manifest.json'),
               (p['report']/'source.tar.gz', root+'/'+cfg['source_relative'])]
    targets += [(p['daily']/'train'/name, root+'/'+cfg['input_relative']+'/'+name)
                for name in manifest['bundle_files']]
    evidence = []
    for local, uri in targets:
        record = remote_hash(session.client('s3'), uri)
        if record['sha256'] != sha256(local):
            raise ValueError('S3 snapshot differs: '+uri)
        evidence.append(record)
    # The frozen snapshot was made with a dirty tree. Never call it pristine commit code.
    scientific = ['src/reference/daily.py', 'src/reference/model.py',
                  'src/reference/provenance.py', 'src/features/run.py',
                  'tools/extract_reference_features.py']
    checked = {}
    for name in scientific:
        if name in manifest['code_revision']['files']:
            if sha256(ROOT/name) != manifest['code_revision']['files'][name]:
                raise ValueError('Scientific source differs from snapshot: '+name)
            checked[name] = sha256(ROOT/name)
    out = dict(schema_version=1, checked_utc=datetime.now(timezone.utc).isoformat(),
               run_id=cfg['run_id'], source_commit=COMMIT,
               snapshot_dirty=manifest['code_revision']['dirty'],
               source_archive_sha256=sha256(p['report']/'source.tar.gz'),
               input_manifest_sha256=sha256(p['report']/'input_manifest.json'),
               snapshot_origin='C:/working/a', objects=evidence,
               timestamp_note='S3 LastModified is the measured upload timestamp; no assumed 00:38 timestamp.',
               scientific_source_hashes=checked, train_bundle_modified=False, passed=True)
    write_json(p['report']/'validator_input_verification.json', out)
    print('Verified actual S3 bytes for', len(evidence), 'frozen objects', flush=True)
    return out


def valid(cfg, p, pipeline, session):
    check_snapshot(p)
    source = ROOT/'reports/reference_baseline'/cfg['source_run']
    historical = read(source/'run_manifest.json')
    if sha256(source/'pipeline_config.yaml') != historical['pipeline_config_sha256']:
        raise ValueError('Historical config differs')
    refconfig = p['daily']/'train/reference_config.yaml'
    hourly = p['hourly']/'valid'
    bundle = p['daily']/'valid'
    # Only explicit subjects; never scan the legacy data/features/ trees.
    commands = []
    for dataset, sid in [('cops', 'COPS-7'), ('nhanes', 'NHANES-81262')]:
        output = hourly/dataset/(sid+'.csv')
        if output.exists():
            m = read(output.with_suffix('.manifest.json'))
            if m['hourly_sha256'] != sha256(output) or m['split'] != 'valid':
                raise ValueError('Existing validation hourly identity/hash mismatch')
            continue
        command = [sys.executable, '-u', '-X', 'utf8', str(ROOT/'tools/extract_reference_features.py'),
                   '--dataset', dataset, '--ids', sid,
                   '--clean-root', str(ROOT/'data/clean/reference-runs'/cfg['source_run']),
                   '--clean-manifest-root', str(ROOT/'data/clean/reference-runs'/cfg['source_run']),
                   '--out', str(hourly), '--config', str(refconfig),
                   '--pipeline-config', str(source/'pipeline_config.yaml')]
        commands.append(command)
        subprocess.run(command, check=True)
    actual = set()
    for f in hourly.rglob('*.csv'):
        m = read(f.with_suffix('.manifest.json'))
        actual.add(m['subject_id'])
        if m['split'] != 'valid' or m['hourly_sha256'] != sha256(f):
            raise ValueError('Validation feature provenance mismatch')
    if actual != EXPECTED:
        raise ValueError('Validation hourly must contain exactly the requested two subjects')
    if not bundle.exists():
        command = [sys.executable, '-u', '-X', 'utf8', str(ROOT/'tools/reference_baseline.py'),
                   'daily', '--hourly-root', str(hourly), '--out', str(bundle), '--config', str(refconfig)]
        commands.append(command)
        subprocess.run(command, check=True)
    daily, _, _, provenance = load_bundle(bundle)
    if set(daily.subject_id) != EXPECTED or not daily.split.eq('valid').all():
        raise ValueError('Validation bundle subjects/split differ')
    if sha256(bundle/'reference_config.yaml') != sha256(refconfig):
        raise ValueError('Validation reference config differs from train')
    frozen = check_snapshot(p)
    if provenance['preprocessing_version'] != frozen['preprocessing_version']:
        raise ValueError('Validation preprocessing differs from train')
    root = pipeline['storage']['s3_root'].rstrip('/')
    prefix = root+'/data/daily/reference-v1/'+cfg['run_id']+'/valid'
    uploads = []
    for file in sorted(bundle.iterdir()):
        if file.is_file():
            uri = prefix+'/'+file.name
            upload_file(session.client('s3'), file, uri)
            actual = remote_hash(session.client('s3'), uri)
            if actual['sha256'] != sha256(file):
                raise ValueError('Validation upload checksum mismatch')
            uploads.append(actual)
    result = dict(schema_version=1, run_id=cfg['run_id'], data_origin='real', is_demo=False,
                  selected_subjects=sorted(EXPECTED), split='valid', fit_performed=False,
                  valid_bundle_s3_uri=prefix+'/', manifest_s3_uri=prefix+'/bundle.manifest.json',
                  manifest_sha256=sha256(bundle/'bundle.manifest.json'),
                  reference_config_sha256=sha256(refconfig), preprocessing_version=provenance['preprocessing_version'],
                  files=uploads, commands=commands, coverage=read(bundle/'coverage_report.json'),
                  train_bundle_modified=False, test_opened=False)
    report = p['report']/'validator_valid_bundle.json'
    if report.exists():
        old = read(report)
        if old['manifest_sha256'] != result['manifest_sha256'] or old['selected_subjects'] != result['selected_subjects']:
            raise ValueError('Do not replace validation bundle provenance')
    else:
        write_json(report, result)
    upload_file(session.client('s3'), p['report']/'validator_valid_bundle.json', prefix+'/validator_valid_bundle.json')
    print('Verified and uploaded validation bundle:', prefix, flush=True)


def provenance(cfg, p, pipeline, session):
    """Package actual existing QC/evidence beside the immutable valid bundle."""
    bundle = p['daily']/'valid'
    daily, _, _, prov = load_bundle(bundle)
    if set(daily.subject_id) != EXPECTED or not daily.split.eq('valid').all():
        raise ValueError('Unexpected validation subjects')
    clean = ROOT/'data/clean/reference-runs'/cfg['source_run']
    prefix = pipeline['storage']['s3_root'].rstrip('/')+'/data/daily/reference-v1/'+cfg['run_id']+'/valid/provenance'
    records = []
    for m in prov['sources']:
        ds, sid = m['dataset'], m['subject_id']
        hourly = p['hourly']/'valid'/ds/(sid+'.csv')
        source_files = [clean/ds/(sid+'.qc.json'), clean/ds/(sid+'.clean.manifest.json'),
                        hourly, hourly.with_suffix('.manifest.json')]
        if sha256(source_files[0]) != m['qc_sha256'] or sha256(hourly) != m['hourly_sha256']:
            raise ValueError('Validation evidence hash changed')
        if read(source_files[1]) != m['clean_run_evidence']:
            raise ValueError('Validation clean manifest changed')
        for file in source_files:
            uri = prefix+'/'+ds+'/'+file.name
            upload_file(session.client('s3'), file, uri)
            records.append(dict(uri=uri, sha256=sha256(file), local_path=str(file)))
    manifest = dict(schema_version=1, data_origin='real', is_demo=False,
                    selected_subjects=sorted(EXPECTED), split='valid', files=records,
                    valid_bundle_manifest_sha256=sha256(bundle/'bundle.manifest.json'),
                    reference_config_sha256=sha256(bundle/'reference_config.yaml'),
                    preprocessing_version=prov['preprocessing_version'], test_opened=False)
    path = p['report']/'validator_provenance_manifest.json'
    write_json(path, manifest)
    upload_file(session.client('s3'), path, prefix+'/manifest.json')
    print('Uploaded actual validation QC, clean manifests and versioned hourly evidence', flush=True)


def handoff(cfg, p, pipeline, session):
    check_snapshot(p)
    sm = session.client('sagemaker')
    try:
        job = sm.describe_training_job(TrainingJobName=cfg['job_name'])
    except ClientError as e:
        if e.response['Error']['Code'] not in ['ValidationException', 'ResourceNotFound'] or 'not found' not in str(e).lower():
            raise
        job = None
    if job:
        train = [v for v in job['InputDataConfig'] if v['ChannelName'] == 'train']
        expected_uri = pipeline['storage']['s3_root'].rstrip('/')+'/'+cfg['input_relative']
        if len(train) != 1 or train[0]['DataSource']['S3DataSource']['S3Uri'] != expected_uri:
            raise ValueError('Existing AWS job has different inputs')
        if job.get('Environment',{}).get('REFERENCE_INPUT_MANIFEST_SHA256') != sha256(p['report']/'input_manifest.json'):
            raise ValueError('Existing AWS job has different manifest')
    q = session.client('service-quotas')
    quota = q.get_service_quota(ServiceCode='sagemaker', QuotaCode=cfg['training_quota_code'])['Quota']
    req = read(p['report']/'quota_request.json')
    request = q.get_requested_service_quota_change(RequestId=req['Id'])['RequestedQuota']
    local = p['model']/'local_py312_compat'
    from src.reference.model import load_reference
    baseline = load_reference(local/'baseline.json')
    _, _, train_cfg, train_prov = load_bundle(p['daily']/'train')
    if baseline['config'] != train_cfg or baseline['provenance'] != train_prov:
        raise ValueError('Local compatibility artifact has different input provenance/config')
    runtime = read(local/'runtime_metadata.json')
    if runtime['baseline_sha256'] != sha256(local/'baseline.json'):
        raise ValueError('Local baseline differs from recorded runtime hash')
    out = dict(schema_version=1, checked_utc=datetime.now(timezone.utc).isoformat(), run_id=cfg['run_id'],
               source_commit=COMMIT, snapshot_dirty=True, region=pipeline['storage']['region'],
               training_job_name=cfg['job_name'], training_status=job['TrainingJobStatus'] if job else 'NOT_SUBMITTED',
               training_job_arn=job['TrainingJobArn'] if job else None,
               aws_model_artifact_s3_uri=job.get('ModelArtifacts',{}).get('S3ModelArtifacts') if job else None,
               aws_model_archive_sha256=None, aws_baseline_json_sha256=None,
               execution_role_arn=cfg['role_arn'], applied_quota=quota['Value'],
               quota_request_id=req['Id'], quota_request_status=request['Status'], case_id=request.get('CaseId'),
               input_verification=read(p['report']/'validator_input_verification.json'),
               validation_bundle=read(p['report']/'validator_valid_bundle.json'),
               validation_provenance_manifest=read(p['report']/'validator_provenance_manifest.json'),
               local_only=dict(status='LOCAL_COMPATIBILITY_ARTIFACT_NOT_AWS_TRAINING',
                   path=str(local/'baseline.json'), baseline_json_sha256=sha256(local/'baseline.json'),
                   runtime=runtime, parity=read(p['report']/'local_dependency_parity.json'),
                   input_provenance_and_config_match_frozen_bundle=True,
                   provenance_note='Existing runtime env fields are null; not backfilled. Embedded input provenance/config were independently checked against the frozen bundle.',
                   counts={ds:{metric:value['summary'] for metric,value in metrics.items()} for ds,metrics in baseline['references'].items()}),
               test_opened=False, endpoint_created=False)
    if job and job['TrainingJobStatus'] == 'Completed':
        cloud = p['model']/'aws'
        if (cloud/'model.tar.gz').exists():
            out['aws_model_archive_sha256'] = sha256(cloud/'model.tar.gz')
            out['aws_baseline_json_sha256'] = sha256(cloud/'baseline.json')
    write_json(p['report']/'VALIDATOR_HANDOFF.json', out)
    print(json.dumps({k:out[k] for k in ['training_status','training_job_arn','applied_quota','quota_request_status','case_id']}, indent=2))


def publish(cfg, p, pipeline, session):
    """Publish explicitly LOCAL results and a timestamped handoff, never AWS output."""
    check_snapshot(p)
    root = pipeline['storage']['s3_root'].rstrip('/')
    model_prefix = root+'/data/models/reference-v1/'+cfg['run_id']+'/local_py312_compat'
    model = p['model']/'local_py312_compat'
    records = []
    for name in ['baseline.json', 'fit_report.json', 'runtime_metadata.json']:
        uri = model_prefix+'/'+name
        upload_file(session.client('s3'), model/name, uri)
        records.append(dict(uri=uri, sha256=sha256(model/name), scope='LOCAL_NOT_AWS_TRAINING'))
    # Every delivery gets a fresh evidence namespace; prior handoffs remain untouched.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    prefix = root+'/reports/reference_baseline/'+cfg['run_id']+'/validator-handoff/'+stamp
    names = ['VALIDATOR_HANDOFF.json', 'VALIDATOR_HANDOFF.md', 'validator_input_verification.json',
             'validator_valid_bundle.json', 'validator_provenance_manifest.json', 'local_dependency_parity.json',
             'analysis_VALIDATION_LOCAL_PY312_REAL.json', 'aws_preflight.json', 'resolved_config.json',
             'execution_role_trust.json', 'execution_role_permissions.json']
    for name in names:
        path = p['report']/name
        upload_file(session.client('s3'), path, prefix+'/'+name)
        records.append(dict(uri=prefix+'/'+name, sha256=sha256(path)))
    for path in [ROOT/'config/sagemaker_reference_pilot.yaml', ROOT/'aws/reference/requirements-prebuilt.txt']:
        uri = prefix+'/'+path.name
        upload_file(session.client('s3'), path, uri)
        records.append(dict(uri=uri, sha256=sha256(path)))
    report = p['report']/'validator_delivery_manifest.json'
    write_json(report, dict(schema_version=1, run_id=cfg['run_id'], files=records,
                           local_results_are_not_aws_training=True))
    upload_file(session.client('s3'), report, prefix+'/delivery_manifest.json')
    print('Published validator evidence:', prefix+'/delivery_manifest.json', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['verify', 'valid', 'provenance', 'handoff', 'publish'], required=True)
    parser.add_argument('--config', type=Path, default=ROOT/'config/sagemaker_reference_pilot.yaml')
    args = parser.parse_args()
    globals()[args.stage](*context(args.config))

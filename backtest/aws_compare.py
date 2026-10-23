"""Compare a SageMaker (AWS) reference artifact with the local baseline that was sent.

  python -X utf8 -m backtest.aws_compare --aws-artifact s3://.../output/model.tar.gz --aws-sha256 <sha of that file>
  python -X utf8 -m backtest.aws_compare --aws-artifact path/to/baseline.json --aws-sha256 <sha>

--aws-sha256 is the SHA256 of the file handed over (model.tar.gz or baseline.json); it is checked before anything
else. File hashes and artifact_id always differ between runs (created_utc, code_version), so equality is judged on
deterministic content. More train people with everything else equal is a NEW version and is validated again.
Read-only: never submits jobs or writes to S3.
"""
import argparse
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from src.reference.common import ROOT, clean_json, fingerprint, sha256, write_json
from src.reference.daily import METRICS
from src.reference.io import load_bundle
from src.reference.model import load_reference

from backtest.evaluators.report_builder import update_registry
from backtest.runners.data_loader import (DEFAULT_CONFIG, check_approved, load_backtest_config, materialize,
                                          pipeline_config, real_paths)

COHORTS = ['cops', 'nhanes']
WANTED = {'baseline.json', 'fit_report.json'}
MAX_MEMBER_BYTES = 256 << 20
ROUNDOFF = 1e-12


# ---------------------------------------------------------------------------
# Fetch + safe extraction
# ---------------------------------------------------------------------------
def fetch(src, expected_sha, dest_dir):
    """Snapshot the handed-over file into the workspace and verify the SHA256 the sender gave."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = PurePosixPath(str(src).rstrip('/')).name if str(src).startswith('s3://') else Path(src).name
    local = dest_dir / name
    if str(src).startswith('s3://'):
        from src import storage
        storage.configure(pipeline_config()['storage'])
        storage.download(str(src), local)
    else:
        shutil.copyfile(src, local)
    actual = sha256(local)
    if actual != expected_sha:
        raise ValueError(f'AWS artifact SHA256 {actual} != provided {expected_sha}')
    return local, actual


def safe_extract(archive, dest):
    """Only regular top-level baseline.json/fit_report.json; any link, absolute or '..' member rejects the archive."""
    dest, out = Path(dest), {}
    with tarfile.open(archive, 'r:*') as tar:
        for m in tar.getmembers():
            p = PurePosixPath(m.name.replace('\\', '/'))
            if p.is_absolute() or '..' in p.parts or (p.parts and ':' in p.parts[0]):
                raise ValueError(f'Unsafe path in archive: {m.name!r}')
            if m.issym() or m.islnk():
                raise ValueError(f'Link in archive: {m.name!r}')
            parts = [x for x in p.parts if x != '.']
            if len(parts) != 1 or parts[0] not in WANTED:
                continue
            if not m.isfile() or m.size > MAX_MEMBER_BYTES:
                raise ValueError(f'Not a regular file or too large: {m.name!r}')
            data = tar.extractfile(m).read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError(f'Member too large: {m.name!r}')
            target = dest / parts[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            out[parts[0]] = target
    if 'baseline.json' not in out:
        raise ValueError('Archive has no top-level baseline.json')
    return out


def unpack(local, dest):
    if local.suffix == '.json':
        return local
    return safe_extract(local, dest)['baseline.json']


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def policy_fingerprint(cfg):
    """Config without naming/paths: equal policies may carry different baseline_version labels."""
    return fingerprint({k: v for k, v in cfg.items() if k not in ('baseline_version', 'paths')})


def _entries(artifact):
    return {(ds, m, e['subject_id'], e['day']): e['value']
            for ds in COHORTS for m in METRICS for e in artifact['references'][ds][m]['entries']}


def _subjects(keys):
    return {(k[0], k[2]) for k in keys}


def _provenance(p):
    return {k: v for k, v in p.items() if k not in ('training_bundle_only', 'sources')}


def _rel(a, b):
    return abs(a - b) / max(abs(a), abs(b)) if max(abs(a), abs(b)) else 0.


def compare(local, aws):
    core = {}
    for name, get in [('preprocessing_version', lambda a: a['preprocessing_version']),
                      ('feature_contract', lambda a: a['config']['feature_contract']),
                      ('metrics', lambda a: a['metrics']), ('data_kind', lambda a: a['data_kind']),
                      ('fit_split', lambda a: a['fit_split']), ('roster_hash', lambda a: a['roster_hash']),
                      ('schema_version', lambda a: a['schema_version']), ('kind', lambda a: a['kind']),
                      ('policy_config_hash', lambda a: policy_fingerprint(a['config']))]:
        core[name] = dict(local=get(local), aws=get(aws), equal=get(local) == get(aws))
    exact_config = dict(local=local['config_hash'], aws=aws['config_hash'], equal=local['config_hash'] == aws['config_hash'])
    le, ae = _entries(local), _entries(aws)
    changed = [dict(key=list(k), local=le[k], aws=ae[k], relative=_rel(le[k], ae[k])) for k in sorted(set(le) & set(ae)) if le[k] != ae[k]]
    added, removed = sorted(set(ae) - set(le)), sorted(set(le) - set(ae))
    new_subjects = sorted(_subjects(added) - _subjects(le))
    summary_diffs = []
    for ds in COHORTS:
        for m in METRICS:
            a, b = local['references'][ds][m]['summary'], aws['references'][ds][m]['summary']
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    summary_diffs.append(dict(dataset=ds, metric=m, field=k, local=a.get(k), aws=b.get(k)))
    provenance_equal = _provenance(local['provenance']) == _provenance(aws['provenance'])
    report = dict(core=core, exact_config_hash=exact_config, training_daily_hash=dict(
        local=local['training_daily_hash'], aws=aws['training_daily_hash'],
        equal=local['training_daily_hash'] == aws['training_daily_hash']),
        provenance_equal_except_training_bundle_only_and_sources=provenance_equal,
        n_entries=dict(local=len(le), aws=len(ae)), changed_values=changed[:50], n_changed_values=len(changed),
        added_entries=len(added), removed_entries=len(removed), new_subjects=[list(s) for s in new_subjects],
        summary_diffs=summary_diffs[:100], n_summary_diffs=len(summary_diffs))
    if not all(c['equal'] for c in core.values()):
        category, reason = 'different_inputs', 'core contract/policy/preprocessing/roster differs'
    elif changed:
        roundoff = all(c['relative'] <= ROUNDOFF for c in changed)
        category = 'same_inputs_mismatch'
        reason = (f'{len(changed)} shared (subject, day) values differ only at float round-off (<= {ROUNDOFF:g}); '
                  'likely a different CSV float parser (pandas version)' if roundoff
                  else f'{len(changed)} shared (subject, day) values changed')
        report['mismatch_kind'] = 'float_roundoff_only' if roundoff else 'value_changed'
    elif not added and not removed:
        if summary_diffs:
            category, reason = 'same_inputs_mismatch', 'identical entries but different reference summaries'
            report['mismatch_kind'] = 'summary_differs'
        elif not report['training_daily_hash']['equal']:
            category, reason = 'same_inputs_mismatch', 'identical entries but training_daily_hash differs (non-ok rows differ)'
            report['mismatch_kind'] = 'training_rows_differ'
        else:
            category = 'same_inputs_match'
            reason = 'same inputs, identical summaries' + ('' if exact_config['equal'] else '; only baseline_version/paths differ')
    elif not removed and new_subjects and _subjects(added) <= set(new_subjects):
        category, reason = 'new_version_more_train', f'{len(new_subjects)} more train participants, existing values unchanged'
    else:
        category = 'different_inputs'
        reason = ('existing participants gained/lost days' if not removed else f'{len(removed)} local entries missing in AWS')
    report.update(category=category, reason=reason)
    return clean_json(report)


def valid_bundle_check(valid_daily, aws):
    if valid_daily is None:
        return 'blocked_no_valid_bundle'
    if not valid_daily.preprocessing_version.eq(aws['preprocessing_version']).all():
        return 'blocked_valid_bundle_preprocessing_mismatch'
    if not valid_daily.config_hash.eq(aws['config_hash']).all():
        return 'blocked_valid_bundle_config_hash_mismatch'
    return 'ok'


def default_validate(run_id, baseline, approved_sha, train_bundle, valid_bundle):
    from backtest.run import run
    results, out = run('real', run_id=run_id, tracks=['valid', 'audit', 'loo', 'stability', 'walkforward'],
                       baseline=baseline, approved_sha=approved_sha, train_bundle=train_bundle,
                       valid_bundle=valid_bundle, overrides={'real': {'source': 'aws'}})
    return dict(run_id=run_id, overall=results['overall'], output=str(out),
                audit={k: v['status'] for k, v in results['tracks']['audit']['categories'].items()})


def markdown(doc):
    c = doc['comparison']
    lines = [f"# AWS vs local reference — {doc['run_id']}", '', f"**ผล: `{c['category']}`** — {c['reason']}", '',
             '| | local (ส่งไป) | AWS |', '| --- | --- | --- |']
    for k in ['file_sha256', 'baseline_sha256', 'artifact_id', 'baseline_version', 'created_utc', 'git_commit', 'git_dirty']:
        lines.append(f"| {k} | `{doc['local'].get(k)}` | `{doc['aws'].get(k)}` |")
    lines += ['', '| field | equal | local | aws |', '| --- | --- | --- | --- |']
    for k, v in list(c['core'].items()) + [('exact_config_hash', c['exact_config_hash']), ('training_daily_hash', c['training_daily_hash'])]:
        lines.append(f"| {k} | {v['equal']} | `{str(v['local'])[:40]}` | `{str(v['aws'])[:40]}` |")
    lines += ['', f"- entries local/AWS: {c['n_entries']}, เพิ่ม {c['added_entries']}, หาย {c['removed_entries']}, ค่าเปลี่ยน {c['n_changed_values']}",
              f"- คน train ใหม่: {c['new_subjects']}", f"- summary ที่ต่าง: {c['n_summary_diffs']}",
              f"- validation: {doc['validation']}"]
    return '\n'.join(lines) + '\n'


def _identity(path, artifact, file_sha=None):
    cv = artifact.get('code_version', {})
    return dict(path=str(path), file_sha256=file_sha or sha256(path), baseline_sha256=sha256(path),
                artifact_id=artifact['artifact_id'], baseline_version=artifact.get('baseline_version'),
                created_utc=artifact.get('created_utc'), git_commit=cv.get('git_commit'), git_dirty=cv.get('dirty'))


def run_compare(aws_artifact, aws_sha, run_id=None, config_path=DEFAULT_CONFIG, local_baseline=None,
                valid_bundle=None, aws_train_bundle=None, validate=True, pull=False, outputs_root=None,
                workspace_root=None, validate_fn=default_validate, local_approved_sha=None):
    bcfg = load_backtest_config(config_path)
    run_id = run_id or f"aws-compare-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    outputs_root = Path(outputs_root or ROOT / bcfg['paths']['outputs'])
    workspace_root = Path(workspace_root or ROOT / bcfg['paths']['workspace'])
    out = outputs_root / run_id
    if out.exists():
        raise ValueError(f'Output exists: {out}; never overwrite')
    rel = real_paths(bcfg)
    local_path = Path(local_baseline) if local_baseline else materialize(rel['baseline'], workspace_root, pull)
    if local_path is None:
        raise FileNotFoundError(f"Local baseline not found: {rel['baseline']} (use --pull)")
    local_sha = check_approved(local_path, local_approved_sha or bcfg['real']['approved_baseline_sha256'])
    local = load_reference(local_path)
    fetched, file_sha = fetch(aws_artifact, aws_sha, workspace_root / 'aws' / run_id)
    aws_path = unpack(fetched, workspace_root / 'aws' / run_id / 'extracted')
    aws = load_reference(aws_path)
    comparison = compare(local, aws)
    doc = dict(run_id=run_id, created_utc=datetime.now(timezone.utc).isoformat(), aws_input=str(aws_artifact),
               local=_identity(local_path, local), aws=_identity(aws_path, aws, file_sha), comparison=comparison)
    doc['aws']['handed_over_file_sha256'] = file_sha
    validation = dict(status='not_required')
    if comparison['category'] == 'same_inputs_match':
        validation = dict(status='reuse_local_validation', equivalent_to=local_sha)
    elif comparison['category'] == 'new_version_more_train':
        vpath = Path(valid_bundle) if valid_bundle else materialize(rel['valid_bundle'], workspace_root, pull)
        vdaily = load_bundle(vpath)[0] if vpath else None
        status = valid_bundle_check(vdaily, aws)
        validation = dict(status=status, valid_bundle=str(vpath) if vpath else None)
        if status == 'ok' and validate:
            validation.update(status='revalidated', result=validate_fn(
                f'{run_id}-validate', aws_path, doc['aws']['baseline_sha256'], aws_train_bundle, vpath))
        elif status == 'ok':
            validation['status'] = 'validation_required_not_run'
    else:
        validation = dict(status='blocked', reason='not comparable/new inputs: needs its own input snapshot and valid bundle')
    doc['validation'] = validation
    out.mkdir(parents=True)
    write_json(out / 'aws_comparison.json', doc)
    (out / 'aws_comparison.md').write_text(markdown(doc), encoding='utf-8')
    record = dict(run_id=run_id, category=comparison['category'], other=None, validation=validation['status'])
    update_registry(outputs_root, local_sha, source='local', artifact_id=local['artifact_id'],
                    baseline_version=local.get('baseline_version'),
                    comparison=dict(record, other=doc['aws']['baseline_sha256']))
    entry = update_registry(outputs_root, doc['aws']['baseline_sha256'], source='aws', artifact_id=aws['artifact_id'],
                            baseline_version=aws.get('baseline_version'), handed_over_file_sha256=file_sha,
                            comparison=dict(record, other=local_sha),
                            equivalent_to=local_sha if comparison['category'] == 'same_inputs_match' else None)
    doc['registry_entry'] = entry
    return doc, out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--aws-artifact', required=True, help='s3://.../model.tar.gz, local model.tar.gz or baseline.json')
    p.add_argument('--aws-sha256', required=True, help='SHA256 of the handed-over file itself')
    p.add_argument('--run-id')
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--local-baseline', type=Path, help='default: approved baseline from config')
    p.add_argument('--valid-bundle', type=Path, help='valid bundle for re-validation (same run as the AWS fit)')
    p.add_argument('--aws-train-bundle', type=Path, help='train bundle used by AWS (enables hash chain audit)')
    p.add_argument('--no-validate', action='store_true')
    p.add_argument('--pull', action='store_true')
    a = p.parse_args(argv)
    doc, out = run_compare(a.aws_artifact, a.aws_sha256, a.run_id, a.config, a.local_baseline, a.valid_bundle,
                           a.aws_train_bundle, not a.no_validate, a.pull)
    c = doc['comparison']
    print(f"{c['category']}: {c['reason']}\n  local baseline {doc['local']['baseline_sha256']}\n  AWS   baseline "
          f"{doc['aws']['baseline_sha256']} (handed-over file {doc['aws']['handed_over_file_sha256']})\n"
          f"  validation: {doc['validation']['status']}\n  -> {out}")
    return 0 if c['category'] in ('same_inputs_match', 'new_version_more_train') and doc['validation']['status'] not in ('blocked',) else 1


if __name__ == '__main__':
    sys.exit(main())

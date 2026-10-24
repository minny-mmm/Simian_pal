"""Write a backtest run as results.json, per-table CSVs, summary.md and an offline dashboard.html
(inline SVG, no CDN/network)."""
import html
import json
from pathlib import Path

import pandas as pd

from src.reference.common import clean_json, write_json

TABLES = {  # csv name -> path inside results
    'evaluability': ('evaluability',),
    'gates': ('gates',),
    'jackknife': ('tracks', 'stability', 'jackknife'),
    'bootstrap': ('tracks', 'stability', 'bootstrap'),
    'sensitivity': ('tracks', 'stability', 'sensitivity'),
    'deviation_spread': ('tracks', 'stability', 'deviation_spread'),
    'loo_train_rows': ('tracks', 'loo', 'rows'),
    'valid_rows': ('tracks', 'valid', 'rows'),
    'first_usable': ('tracks', 'walkforward', 'first_usable'),
    'within_subject_stability': ('tracks', 'walkforward', 'within_subject'),
    'known_answer': ('tracks', 'valid', 'known_answer'),
    'audit_categories': ('tracks', 'audit', 'category_table'),
}


def _get(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _fmt(v):
    if v is None:
        return '—'
    if isinstance(v, float):
        return f'{v:.4g}'
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _html_table(rows, columns=None, limit=200):
    if not rows:
        return '<p class="muted">ไม่มีข้อมูล</p>'
    columns = columns or list(rows[0])
    head = ''.join(f'<th>{html.escape(c)}</th>' for c in columns)
    body = ''.join('<tr>' + ''.join(f'<td class="{_cls(r.get(c))}">{html.escape(_fmt(r.get(c)))}</td>' for c in columns) + '</tr>'
                   for r in rows[:limit])
    more = f'<p class="muted">แสดง {limit} จาก {len(rows)} แถว (ครบใน CSV)</p>' if len(rows) > limit else ''
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}'


def _cls(v):
    return {'pass': 'pass', 'fail': 'fail', 'not_evaluable': 'warn', True: 'pass', False: 'fail'}.get(v, '') if isinstance(v, (str, bool)) else ''


def _strip_svg(rows, metric):
    """Dot strip of valid standardized deviations per (query cohort, reference), clipped to +-6."""
    f = pd.DataFrame(rows)
    if f.empty:
        return ''
    f = f[f.metric.eq(metric) & f.standardized_deviation.notna()]
    lanes = sorted({(q, r) for q, r in zip(f.dataset, f.reference)})
    if not lanes:
        return '<p class="muted">ไม่มี standardized deviation ที่คำนวณได้</p>'
    w, lh, left = 640, 34, 150
    hgt = lh * len(lanes) + 30

    def x(z):
        return left + (max(-6, min(6, z)) + 6) / 12 * (w - left - 20)

    parts = [f'<svg viewBox="0 0 {w} {hgt}" role="img" aria-label="{html.escape(metric)}">']
    for z in [-4, -2, 0, 2, 4]:
        parts.append(f'<line x1="{x(z):.1f}" x2="{x(z):.1f}" y1="5" y2="{hgt - 22}" class="grid{" zero" if z == 0 else ""}"/>'
                     f'<text x="{x(z):.1f}" y="{hgt - 6}" class="tick">{z:+d}</text>')
    for i, (q, r) in enumerate(lanes):
        y = 20 + i * lh
        parts.append(f'<text x="8" y="{y + 4}" class="lane">{q.upper()} vs ref {r.upper()}</text>')
        for z in f[f.dataset.eq(q) & f.reference.eq(r)].standardized_deviation:
            parts.append(f'<circle cx="{x(z):.1f}" cy="{y}" r="4" class="dot {q}"><title>{z:.3f}</title></circle>')
    parts.append('</svg>')
    return ''.join(parts)


CSS = """
:root{--bg:#fff;--fg:#1d2330;--muted:#667085;--line:#d9dee7;--pass:#1a7f4b;--fail:#b42318;--warn:#a15c07;--cops:#3b6fd8;--nhanes:#c2410c}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e9ef;--muted:#98a2b3;--line:#2e343e;--pass:#4ade80;--fail:#f87171;--warn:#fbbf24;--cops:#7aa2f7;--nhanes:#fb923c}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"Noto Sans Thai",sans-serif;margin:0 auto;max-width:1100px;padding:16px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}
.caveat{border-left:4px solid var(--warn);padding:8px 12px;background:color-mix(in srgb,var(--warn) 8%,transparent)}
.scroll{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;width:100%}
th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
.pass{color:var(--pass);font-weight:600}.fail{color:var(--fail);font-weight:600}.warn{color:var(--warn)}.muted{color:var(--muted)}
svg{width:100%;max-width:640px;height:auto}.grid{stroke:var(--line)}.zero{stroke:var(--muted)}
.tick,.lane{fill:var(--muted);font-size:11px}.tick{text-anchor:middle}.dot{fill-opacity:.6}.dot.cops{fill:var(--cops)}.dot.nhanes{fill:var(--nhanes)}
"""


def build_html(results):
    t = results['tracks']
    sections = [
        ('Gates (pre-registered)', _html_table(results['gates'])),
        ('Evaluability — ข้อมูลพอทำ backtest ระดับไหน', _html_table(results['evaluability'])),
        ('ระดับ 1: Jackknife (สรุปต่อ cell)', _html_table(_get(t, ('stability', 'jackknife_summary')))),
        ('ระดับ 1: Subject bootstrap ของ median', _html_table(_get(t, ('stability', 'bootstrap')))),
        ('ระดับ 1: Sensitivity (sensitivity_only_not_production)', _html_table(_get(t, ('stability', 'sensitivity')))),
        ('ระดับ 1: Train LOO distribution', _html_table(_get(t, ('loo', 'distribution')))),
        ('ระดับ 2: No-lookahead', _html_table([dict(check=k, **{kk: vv for kk, vv in v.items() if kk != 'mismatches'})
                                              for k, v in (_get(t, ('walkforward', 'no_lookahead')) or {}).items()])),
        ('ระดับ 2: Within-subject stability', _html_table(_get(t, ('walkforward', 'stability_summary')))),
        ('ระดับ 3: Valid distribution', _html_table(_get(t, ('valid', 'distribution')))),
        ('ระดับ 3: Device-confound diagnostic', _html_table(_get(t, ('valid', 'device_confound', 'gaps')))
         + ''.join(f'<p class="warn">⚠ {html.escape(f)}</p>' for f in (_get(t, ('valid', 'device_confound', 'flags')) or []))),
        ('ระดับ 3: Known-answer (synthetic)', _html_table(_get(t, ('valid', 'known_answer')))),
        ('Validation audit — หมวดที่ตรวจ', _html_table(_get(t, ('audit', 'category_table')))),
        ('Validation audit — กรณี null', _html_table([dict(case=k, **v) for k, v in (_get(t, ('audit', 'null_cases')) or {}).items()])),
        ('Validation audit — previous_day', _html_table([dict(previous_day_status=k, **v) for k, v in (_get(t, ('audit', 'previous_day_cases')) or {}).items()])),
        ('Validation audit — เทียบรายงานทางการ / float parse',
         _html_table([_get(t, ('audit', 'official_reports')) or {}]) + _html_table([_get(t, ('audit', 'float_parse_check')) or {}])
         + ''.join(f'<p class="warn">ข้อจำกัด: {html.escape(x)}</p>' for x in (_get(t, ('audit', 'limitations')) or []))),
        ('Validation audit — ตัวอย่างที่ไม่ผ่าน', _html_table([dict(category=k, **e) for k, c in (_get(t, ('audit', 'categories')) or {}).items()
                                                                for e in c['examples']])),
        ('ระดับ 4: Test hold-out', _html_table([_get(t, ('test',)) or {}])),
        ('Track B: Symptom CV (exploratory)', _html_table([{k: v for k, v in (_get(t, ('symptom',)) or {}).items()
                                                           if k in ('status', 'reason', 'source', 'n_folds')}])
         + _html_table([{k: v for k, v in (_get(t, ('symptom', 'cv')) or {}).items() if k != 'per_subject'}])
         + _html_table([{k: v for k, v in (_get(t, ('symptom', 'valid')) or {}).items() if k != 'per_subject'}])),
    ]
    charts = ''.join(f'<h3>{html.escape(m)}</h3>{_strip_svg(_get(t, ("valid", "rows")) or [], m)}'
                     for m in sorted({r["metric"] for r in (_get(t, ("valid", "rows")) or [])}))
    body = ''.join(f'<h2>{html.escape(h)}</h2>{c}' for h, c in sections)
    return (f'<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Backtest {html.escape(results["run_id"])}</title><style>{CSS}</style></head><body>'
            f'<h1>Backtest {html.escape(results["run_id"])} <span class="muted">({html.escape(results["mode"])})</span></h1>'
            f'<p class="caveat">{html.escape(results["caveat"])}</p>'
            f'<p>Overall: <span class="{_cls(results["overall"].split("_")[0])}">{html.escape(results["overall"])}</span>'
            f' · protected paths unchanged: {html.escape(_fmt(results.get("protected_paths_unchanged")))}</p>'
            f'{body}<h2>ระดับ 3: Standardized deviation ของ valid (clip ±6)</h2>{charts}</body></html>')


def build_markdown(results):
    lines = [f"# Backtest {results['run_id']} ({results['mode']})", '', f"> {results['caveat']}", '',
             f"- Overall: **{results['overall']}**", f"- Protected paths unchanged: {results.get('protected_paths_unchanged')}",
             f"- Created (UTC): {results['created_utc']}", '', '## Gates', '', '| gate | status | value | threshold |',
             '| --- | --- | --- | --- |']
    lines += [f"| {g['gate']} | {g['status']} | {_fmt(g.get('value'))} | {_fmt(g.get('threshold'))} |" for g in results['gates']]
    lines += ['', '## Evaluability', '', '| dataset | metric | ref people | full | LOO/jackknife | bootstrap | valid | test |',
              '| --- | --- | ---: | --- | --- | --- | --- | --- |']
    lines += [f"| {e['dataset']} | {e['metric']} | {e['n_reference_participants']} | {e['full_reference_evaluable']} | "
              f"{e['loo_jackknife_evaluable']} | {e['bootstrap_evaluable']} | {e['valid_level']} | {e['test_level']} |"
              for e in results['evaluability']]
    audit = _get(results, ('tracks', 'audit'))
    if audit:
        lines += ['', '## Validation audit', '', f"- baseline SHA256: `{audit['baseline_sha256']}` (หลัง audit: `{audit['sha_after']}`)",
                  f"- fit ระหว่าง validation: {audit['fit_called_during_validation']}", '',
                  '| หมวด | status | checked | failed |', '| --- | --- | ---: | ---: |']
        lines += [f"| {c['category']} | {c['status']} | {c['n_checked']} | {c['n_failed']} |" for c in audit['category_table']]
        if audit.get('null_cases'):
            lines += ['', '| กรณี null | exercised | violations | status |', '| --- | ---: | ---: | --- |']
            lines += [f"| {k} | {v['n_exercised']} | {v['n_violations']} | {v['status']} |" for k, v in audit['null_cases'].items()]
        if audit.get('previous_day_cases'):
            lines += ['', '| previous_day | n | status |', '| --- | ---: | --- |']
            lines += [f"| {k} | {v['n']} | {v['status']} |" for k, v in audit['previous_day_cases'].items()]
        if audit.get('official_reports'):
            lines += ['', f"- เทียบ analysis ทางการ: {_fmt(audit['official_reports'])}"]
        if audit.get('float_parse_check'):
            lines += [f"- float parse (pandas default vs round_trip): {_fmt(audit['float_parse_check'])}"]
        lines += [f'- ข้อจำกัด: {x}' for x in audit.get('limitations', [])]
    flags = _get(results, ('tracks', 'valid', 'device_confound', 'flags')) or []
    if flags:
        lines += ['', '## Device-confound flags', ''] + [f'- {f}' for f in flags]
    cells = results.get('test_ready_cells') or []
    if cells:
        lines += ['', '## ความพร้อมเปิด test (ต่อ cohort × ตัวชี้วัดที่ reference ok)', '', '| dataset | metric | ready | เหตุผล |',
                  '| --- | --- | --- | --- |']
        lines += [f"| {c['dataset']} | {c['metric']} | {c['ready']} | {', '.join(c['reasons']) or '—'} |" for c in cells]
    test = _get(results, ('tracks', 'test'))
    if isinstance(test, dict):
        lines += ['', '## Test hold-out', '', f"- status: **{test.get('status')}**"]
        lines += [f'- {_fmt(r)}' for r in (test.get('readiness') or {}).get('reasons', [])]
        if test.get('reason'):
            lines.append(f"- {test['reason']}")
    lines += ['', 'ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline']
    return '\n'.join(lines) + '\n'


def update_registry(outputs_root, baseline_sha, validation_run=None, comparison=None, name='baseline_registry.json', **fields):
    """baseline_registry.json keyed by baseline SHA256: validation is always tied to the exact artifact."""
    path = Path(outputs_root) / name
    registry = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    entry = registry.setdefault(baseline_sha, dict(validation_runs=[], comparisons=[], validation_audit_passed=False))
    entry.update({k: v for k, v in fields.items() if v is not None})
    if validation_run:
        entry['validation_runs'].append(validation_run)
    if comparison:
        entry['comparisons'].append(comparison)
    # Audit correctness only; stability gates are reported per run (overall) and are not implied here.
    entry['validation_audit_passed'] = any(r.get('audit_pass') and r.get('valid_rows') for r in entry['validation_runs'])
    write_json(path, registry)
    return entry


def write_report(out_dir, results):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = clean_json(results)  # numpy scalars -> JSON-native before any formatting
    write_json(out / 'results.json', results)
    for name, path in TABLES.items():
        rows = _get(results, path)
        if rows:
            pd.DataFrame(clean_json(rows)).to_csv(out / f'{name}.csv', index=False)
    (out / 'summary.md').write_text(build_markdown(results), encoding='utf-8')
    (out / 'dashboard.html').write_text(build_html(results), encoding='utf-8')
    return out

"""Generate SYNTHETIC hourly/daily input and reference outputs; no real fit claim."""
import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.reference.common import fingerprint, load_config, write_json
from src.reference.daily import aggregate_daily
from src.reference.io import write_bundle
from src.reference.model import compare_daily, load_reference
from tools.reference_baseline import fit_bundle


def synthetic_inputs(cfg):
    roster, hourly = [], []
    for ds in ['cops','nhanes']:
        for split, count in [('train',6),('valid',1),('test',1)]:
            for participant in range(1,count+1):
                sid = f'SYNTHETIC-{ds.upper()}-{split.upper()}-{participant:02d}'
                roster.append(dict(dataset=ds,subject_id=sid,split=split))
                for day in [0,1]:
                    for hour in range(9,15):
                        windows, walk, rest = 360, 60, 120
                        tremor = (participant + day) * (2 if ds == 'cops' else 1)
                        hourly.append(dict(dataset=ds,subject_id=sid,split=split,day=day,hour=hour,
                            n_windows=windows,valid_min=60.,n_walk=walk,n_clean_walk=30,n_rest=rest,n_tremor=tremor,
                            enmo_median=.01 + .01*participant + .001*day + .0001*hour,
                            step_freq_walk_median=1.2 + .05*participant + .01*day,
                            tremor_frac_of_rest=tremor/rest))
    provenance = dict(verified=True,data_kind='synthetic',preprocessing_version='SYNTHETIC-FIXTURE-v1',
                      feature_contract=cfg['feature_contract'],config_hash=fingerprint(cfg),
                      note='All subjects, signals and values fabricated for software verification; not empirical experiments')
    return pd.DataFrame(hourly),pd.DataFrame(roster),provenance


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'reports/reference_baseline/synthetic_demo')
    p.add_argument('--review',action='store_true',help='DEMO-001 ten-day review outputs, schema validation and offline HTML')
    args=p.parse_args()
    if args.out.exists():
        raise ValueError('Demo output exists; choose a new directory')
    if args.review:
        from tools.reference_demo_review import run_review_demo
        run_review_demo(args.out)
        return
    cfg=load_config()
    hourly,roster,provenance=synthetic_inputs(cfg)
    daily,hour_qc=aggregate_daily(hourly,roster,cfg,provenance)
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    write_bundle(args.out/'bundle',daily,hour_qc,roster,cfg,pipeline,provenance)
    hourly.to_csv(args.out/'hourly_SYNTHETIC.csv',index=False)
    artifact=fit_bundle(args.out/'bundle',args.out/'model')
    # Exercise actual artifact loading, including its integrity checks.
    loaded=load_reference(args.out/'model/baseline.json')
    from src.reference.output import build_analysis
    write_json(args.out/'analysis_SYNTHETIC.json',build_analysis(daily,loaded))
    print(f"SYNTHETIC ONLY: {len(daily)} metric-day rows; artifact {artifact['artifact_id']}; {args.out}")


if __name__=='__main__':
    main()

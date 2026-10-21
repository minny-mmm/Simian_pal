"""Create a train-only local bundle from a verified daily bundle. No network."""
import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.reference.io import load_bundle,write_bundle


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():
        raise ValueError('Output exists; choose a new directory')
    daily,roster,cfg,provenance=load_bundle(a.bundle)
    selected=daily[daily.split.eq('train')]
    if selected.empty:
        raise ValueError('No train records')
    hour_qc=pd.read_csv(a.bundle/'hour_qc.csv')
    pipeline=yaml.safe_load((a.bundle/'pipeline_config.yaml').read_text(encoding='utf-8'))
    provenance={**provenance,'training_bundle_only':True}
    if 'sources' in provenance:
        provenance['sources']=[s for s in provenance['sources'] if s['split']=='train']
    write_bundle(a.out,selected,hour_qc[hour_qc.split.eq('train')],roster,cfg,pipeline,provenance)
    print(f'Train only: {len(selected)} metric-day records, {selected.subject_id.nunique()} participants; no upload')


if __name__=='__main__':
    main()

"""SageMaker BYOC entrypoint; the same fit_bundle used locally. No API calls."""
import argparse
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.reference.io import load_bundle
from tools.reference_baseline import fit_bundle


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',default='train',choices=['train'])
    p.add_argument('--bundle',type=Path,default=Path(os.environ.get('SM_CHANNEL_TRAIN','/opt/ml/input/data/train')))
    p.add_argument('--model-dir',type=Path,default=Path(os.environ.get('SM_MODEL_DIR','/opt/ml/model')))
    a=p.parse_args()
    daily,_,_,provenance=load_bundle(a.bundle)
    if not daily.split.eq('train').all() or not provenance.get('training_bundle_only'):
        raise ValueError('AWS input must be prepared train-only bundle; run prepare_reference_training.py')
    artifact=fit_bundle(a.bundle,a.model_dir)
    print(f"{artifact['data_kind']} reference artifact saved; no classifier/endpoint; fit_split=train")


if __name__=='__main__':
    main()

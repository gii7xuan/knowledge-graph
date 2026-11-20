"""Isolated handoff-revision experiment; prepare freezes inputs without model calls.

python -m evaluation.handoff_revision prepare --output outputs/<new-directory> --limit 3
python -m evaluation.handoff_revision verify --output outputs/<new-directory>
python -m evaluation.handoff_revision run --output outputs/<new-directory>

--data accepts the bound population format (bindings, selected_raw_candidates,
source_database). Every run's verified document index and approved release are
bound by hash; optional source_canonical_text(s) are also checked. Labels never enter review inputs.
The run subcommand is explicit; this module never launches from prepare/verify.
"""
import argparse
import json
from pathlib import Path
from .agent_resolution import prepare,verify,execute,seal
from runs.regress_v8 import DATA


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','verify','run','report'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--data',type=Path,default=DATA)
    parser.add_argument('--limit',type=int,default=3)
    args=parser.parse_args()
    if args.action=='prepare':result=prepare(args.output,limit=args.limit,data_path=args.data,profile='handoff_revision')
    else:
        frozen,_=verify(args.output)
        if frozen.get('profile_name')!='handoff_revision':raise ValueError('this entry requires a handoff_revision freeze')
        result=execute(args.output) if args.action=='run' else seal(args.output) if args.action=='report' else {
            'verified':True,'freeze_hash':frozen['freeze_hash']}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

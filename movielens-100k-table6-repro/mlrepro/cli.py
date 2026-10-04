from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from contextlib import contextmanager

from .artifacts import load_case
from .dataset import BuildOptions, build_case
from .decompose import compute, validate_values, BackendTimeout
from .evaluate import evaluate, verify_reference, render_table
from .fetch import download
from .utils import atomic_write_text, sha256_file

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT/'reference/user_reported_table6.json'
SETTINGS={'train_quantile':0.8,'genre_weight':0.8,'year_weight':0.2,
          'ii_similarity_threshold_strict_gt':0.4,'ii_top_k':20,'minimum_rating':0.0}


class Tee:
    def __init__(self,stream,file):self.stream,self.file=stream,file
    def write(self,text):self.stream.write(text);self.file.write(text);self.file.flush();return len(text)
    def flush(self):self.stream.flush();self.file.flush()
    def isatty(self):return False

@contextmanager
def logged(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',buffering=1) as f:
        oldout,olderr=sys.stdout,sys.stderr
        sys.stdout,sys.stderr=Tee(oldout,f),Tee(olderr,f)
        try:yield
        except BaseException as exc:
            print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
            raise
        finally:sys.stdout,sys.stderr=oldout,olderr


def inspect_case(folder:Path, *, require_protocol:bool=False) -> dict:
    case=load_case(folder)
    if require_protocol:
        for key,val in SETTINGS.items():
            if case.metadata.get(key)!=val:
                raise ValueError(f'Wrong reproduction setting {key}: {case.metadata.get(key)!r}; expected {val!r}')
    report={'case':str(case.root),'graph_sha256':case.graph_sha256,
            'vertices':len(case.nodes),'users':sum(n.node_type=='user' for n in case.nodes),
            'items':sum(n.node_type=='item' for n in case.nodes),'edges':case.edge_count,
            'ui_edges':sum(t=='UI' for t in case.edge_types),'ii_edges':sum(t=='II' for t in case.edge_types),
            'test_edges':len(case.truth),'eligible_test_users':len({u for u,i in case.truth}),
            'settings':{k:case.metadata.get(k) for k in SETTINGS}}
    print(json.dumps(report,indent=2));return report


def import_case(source:Path, target:Path) -> None:
    source=Path(source).resolve();target=Path(target).resolve()
    if source==target:raise ValueError('Source and destination must differ')
    old=load_case(source)
    if target.exists():raise FileExistsError(f'Refusing to overwrite {target}; choose a new directory')
    target.mkdir(parents=True)
    required=('graph.txt','nodes.tsv','edges.tsv','test_edges.tsv','case.json')
    optional=('catalog_items.txt','users.txt','user_item_edges.txt','item_item_edges.txt','evaluation_users.txt')
    for name in required+optional:
        if (source/name).is_file():shutil.copy2(source/name,target/name)
    new=load_case(target)
    if old.graph_sha256!=new.graph_sha256:raise RuntimeError('Graph copy differs')
    copied=[]
    for length in (3,4,5,6):
        if not (source/'values'/f'c{length}_truss.txt').exists():continue
        # A present but invalid/incomplete pair is never silently trusted.
        validate_values(old,length)
        (target/'values').mkdir(exist_ok=True)
        for kind in ('support','truss'):
            name=f'c{length}_{kind}.txt'
            for suffix in ('','.meta.json'):
                shutil.copy2(source/'values'/(name+suffix),target/'values'/(name+suffix))
        validate_values(new,length);copied.append(length)
    info={'source_for_provenance_only':str(source),'graph_sha256':new.graph_sha256,
          'copied_lengths':copied,'contains_symlinks':False,
          'note':'All required bytes copied. Subsequent commands do not read the old project.'}
    atomic_write_text(target/'import_provenance.json',json.dumps(info,indent=2)+'\n')
    print(f'[import] standalone copy created: {target}; verified C{copied}')


def _read_aggregate(folder:Path):
    import csv
    with (folder/'aggregate.tsv').open(encoding='utf-8',newline='') as f:
        rows=list(csv.DictReader(f,delimiter='\t'))
    for r in rows:
        for name in ('average_ndcg','average_precision'):r[name]=float(r[name])
    return rows


def run(args) -> None:
    work=Path(args.work_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    logs=work/'logs';logs.mkdir(exist_ok=True)
    case_dir=work/'case';results=work/'results'
    with logged(logs/'pipeline.log'):
        print(f'[environment] Python {platform.python_version()} executable={sys.executable}')
        print(f'[environment] project={ROOT} work={work} c6_mode={args.c6}')
        if case_dir.exists():
            with logged(logs/'build.log'):
                print('[resume] using local case; not rebuilding or changing graph')
                inspect_case(case_dir,require_protocol=True)
        else:
            with logged(logs/'download.log'):
                ratings,movies=download(ROOT/'data',archive=args.archive)
            with logged(logs/'build.log'):
                build_case(ratings_path=ratings,movies_path=movies,output_dir=case_dir,options=BuildOptions())
                inspect_case(case_dir,require_protocol=True)
        with logged(logs/'decomposition.log'):
            for length in (3,4,5):compute(case_dir,length,timeout=args.backend_timeout)
        with logged(results/'evaluation.log'):
            evaluate(case_dir,results,top_users=args.top_users,cutoff=5,
                     c6_status={'status':'NOT_RUN','reason':'C6 has a separate optional budget; no metric invented'})
        if not args.no_reference_check:
            with logged(logs/'reference.log'):
                if not verify_reference(results,REFERENCE):
                    raise RuntimeError(f'Reference mismatch; inspect {results}/reference_check.json. '
                                       'No data or ranking rule was changed to force a match.')
        if args.c6=='skip':
            print('[C6] NOT_RUN. Four numerical methods finished. Use --c6 run to attempt C6.')
            return
        if args.c6=='reuse':
            validate_values(load_case(case_dir),6)
            status={'status':'CACHED','graph_sha256':load_case(case_dir).graph_sha256}
        else:
            try:
                with logged(logs/'c6.log'):
                    status=compute(case_dir,6,timeout=args.c6_timeout)
            except BackendTimeout:
                status=json.loads((case_dir/'values/c6_status.json').read_text())
                # A real TIMEOUT is recorded, never reported as a measured infinity.
                meta=json.loads((results/'evaluation.json').read_text());meta['c6_status']=status
                atomic_write_text(results/'evaluation.json',json.dumps(meta,indent=2)+'\n')
                render_table(results,_read_aggregate(results),5,status)
                print(f'[C6] TIMEOUT after {status["elapsed_seconds"]:.1f}s; four completed results are retained.')
                print(f'[C6] table6.tsv displays INF with explicit TIMEOUT status. Budget={args.c6_timeout}s.')
                return
        with logged(results/'evaluation.log'):
            evaluate(case_dir,results,top_users=args.top_users,cutoff=5,include_c6=True,c6_status=status)
        if not args.no_reference_check:
            if not verify_reference(results,REFERENCE):raise RuntimeError('Reference mismatch')


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description='Self-contained final MovieLens-100K Table-6 reproduction')
    sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('run',help='build/reuse local case, decompose, evaluate, verify four reference methods')
    r.add_argument('--work-dir',type=Path,default=ROOT/'work')
    r.add_argument('--archive',type=Path,help='optional existing official ml-100k.zip')
    r.add_argument('--top-users',type=int,default=500)
    r.add_argument('--backend-timeout',type=float,default=None,help='per C3/C4/C5 backend seconds')
    r.add_argument('--c6',choices=('skip','run','reuse'),default='skip')
    r.add_argument('--c6-timeout',type=float,default=36400)
    r.add_argument('--no-reference-check',action='store_true',help='only for intentionally different data/cohorts')
    i=sub.add_parser('import-case',help='copy an existing canonical case and verified values into this project')
    i.add_argument('--source',type=Path,required=True);i.add_argument('--output',type=Path,default=ROOT/'work/case')
    b=sub.add_parser('build',help='build the fixed 0.8/0.2 >0.4 top20 full graph from local raw files')
    b.add_argument('--ratings',type=Path,required=True);b.add_argument('--movies',type=Path,required=True)
    b.add_argument('--output',type=Path,default=ROOT/'work/case')
    d=sub.add_parser('decompose');d.add_argument('--case',type=Path,default=ROOT/'work/case')
    d.add_argument('--lengths',default='3,4,5');d.add_argument('--timeout',type=float)
    d.add_argument('--force',action='store_true')
    e=sub.add_parser('evaluate');e.add_argument('--case',type=Path,default=ROOT/'work/case')
    e.add_argument('--output',type=Path,default=ROOT/'work/results')
    e.add_argument('--top-users',type=int,default=500);e.add_argument('--cutoff',type=int,default=5)
    e.add_argument('--include-c6',action='store_true')
    c=sub.add_parser('verify-reference');c.add_argument('--results',type=Path,default=ROOT/'work/results')
    c.add_argument('--reference',type=Path,default=REFERENCE)
    v=sub.add_parser('inspect');v.add_argument('--case',type=Path,default=ROOT/'work/case')
    return p


def main(argv=None) -> int:
    args=parser().parse_args(argv)
    try:
        if args.command=='run':run(args)
        elif args.command=='import-case':import_case(args.source,args.output)
        elif args.command=='build':
            if args.output.exists():raise FileExistsError(f'Refusing to overwrite {args.output}')
            build_case(ratings_path=args.ratings,movies_path=args.movies,output_dir=args.output,options=BuildOptions())
        elif args.command=='decompose':
            for length in sorted(set(int(x) for x in args.lengths.split(','))):
                compute(args.case,length,timeout=args.timeout,force=args.force)
        elif args.command=='evaluate':
            with logged(args.output/'evaluation.log'):
                evaluate(args.case,args.output,top_users=args.top_users,cutoff=args.cutoff,include_c6=args.include_c6)
        elif args.command=='verify-reference':
            return 0 if verify_reference(args.results,args.reference) else 2
        elif args.command=='inspect':inspect_case(args.case)
        return 0
    except KeyboardInterrupt:
        print('Interrupted. Completed values remain reusable.',file=sys.stderr);return 130
    except Exception as exc:
        print(f'error: {type(exc).__name__}: {exc}',file=sys.stderr);return 1

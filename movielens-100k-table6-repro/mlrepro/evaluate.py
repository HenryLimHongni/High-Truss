"""The FINAL full-graph Table-6 protocol; no pivot subgraphs and no AVG method.

C3 direct / C4 typed / C5 MAX use degree padding. ItemKNN uses the full
unobserved warm catalog. Optional C6 preserves the original no-padding rule.
"""
from __future__ import annotations

import csv
import json
import math
import platform
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Mapping

from .artifacts import load_case, load_value_file
from .evidence import (_c3_direct_scores, _c5_evidence, _c6_evidence, c4_typed_evidence,
                       _graph_relations, _rank_with_optional_padding,
                       _itemknn_positive_scores, _itemknn_ranking, _dcg)
from .utils import atomic_write_text, write_tsv, sha256_file

METHODS=('c3-truss-direct','c4-truss-typed','c5-max','itemknn-full-catalog')

@dataclass
class Result:
    method: str
    candidates: set[int]
    scores: dict[int, float]
    ranking: list[int]
    padding: set[int]
    edge_counts: Mapping[int,int] = field(default_factory=dict)
    cycle_counts: Mapping[int,int] = field(default_factory=dict)
    length: int = 0


def metrics(ranking: list[int], truth: set[int], candidates: set[int], padding: set[int], k: int) -> dict:
    if k <= 0: raise ValueError('cutoff must be positive')
    top=ranking[:k]
    if len(top)!=len(set(top)): raise ValueError('duplicate recommendation')
    hits=sum(i in truth for i in top)
    structural_hits=sum(i in truth and i in candidates for i in top)
    candidate_truth=len(candidates & truth)
    relevance=[int(i in truth) for i in top]+[0]*max(0,k-len(top))
    ideal=min(len(truth),k)
    return dict(recommendation_count=len(top), missing_slots=k-len(top),
                structural_candidate_count=len(candidates), candidate_ground_truth=candidate_truth,
                ground_truth_all=len(truth), hits=hits, structural_hits=structural_hits,
                padding_hits=sum(i in truth and i in padding for i in top),
                precision=hits/k, hr=float(hits>0), recall=hits/len(truth) if truth else 0.0,
                candidate_recall=structural_hits/candidate_truth if candidate_truth else 0.0,
                ndcg=_dcg(relevance)/_dcg([1]*ideal) if ideal else 0.0)


def _json(path: Path, value: object) -> None:
    atomic_write_text(path,json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def evaluate(case_dir: Path, output_dir: Path, *, top_users: int=500, cutoff: int=5,
             include_c6: bool=False, c6_status: dict | None=None) -> Path:
    """Evaluate one cutoff using all eligible users up to the requested cap.

    There is NO filtering on candidate/ground-truth coverage and NO result
    selection. Users without structural candidates receive degree padding for
    C3/C4/C5 and still count in the macro-average denominator.
    """
    if cutoff<=0 or top_users<=0: raise ValueError('cutoff and top_users must be positive')
    case=load_case(case_dir); out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    _,degree,user_items,item_users,item_neighbors=_graph_relations(case)
    catalog=tuple(i for i,n in enumerate(case.nodes) if n.node_type=='item')
    by_degree=tuple(sorted(catalog,key=lambda i:(-degree[i],i)))
    truth_by_user=defaultdict(set)
    for u,i in case.truth: truth_by_user[case.node_to_id[u]].add(case.node_to_id[i])
    ordered=sorted(user_items,key=lambda u:(-len(user_items[u]),case.nodes[u].entity_id,u))
    global_rank={u:r for r,u in enumerate(ordered,1)}
    eligible=[u for u in ordered if truth_by_user[u]]
    selected=eligible[:top_users]
    if not selected: raise ValueError('No training users have warm-new test interactions')
    lengths=(3,4,5,6) if include_c6 else (3,4,5)
    weights={l:load_value_file(case,case.root/'values'/f'c{l}_truss.txt',
                              expected_algorithm=f'c{l}_truss') for l in lengths}
    inv={i:1.0/math.sqrt(len(users)) for i,users in item_users.items() if users}
    methods=list(METHODS)
    if include_c6: methods.insert(-1,'c6-max')
    metric_rows=[];ranking_rows=[];user_rows=[]
    start=time.monotonic()
    for selected_rank,u in enumerate(selected,1):
        observed=user_items[u];truth=truth_by_user[u]
        if observed & truth: raise ValueError('Train/test overlap detected')
        def ranked(candidates,scores,pad=True):
            return _rank_with_optional_padding(structural_candidates=candidates,scores=scores,
                      observed=observed,catalog_by_degree=by_degree,degree=degree,
                      target_length=cutoff,pad=pad)
        c3,s3,n3=_c3_direct_scores(observed=observed,item_neighbors=item_neighbors,c3_truss=weights[3])
        r3,p3=ranked(c3,s3)
        e4=c4_typed_evidence(observed=observed,item_neighbors=item_neighbors,c4_truss=weights[4])
        c4=set(e4);s4={i:float(e.max_value) for i,e in e4.items()}
        r4,p4=ranked(c4,s4)  # Final requirement: TRUE, same padding as C3/C5.
        e5=_c5_evidence(user=u,observed=observed,user_items=user_items,item_users=item_users,
                        item_neighbors=item_neighbors,c5_truss=weights[5])
        c5=set(e5);s5={i:float(e.max_value) for i,e in e5.items()}
        r5,p5=ranked(c5,s5)
        rs=[Result('c3-truss-direct',c3,s3,r3,p3,n3),
            Result('c4-truss-typed',c4,s4,r4,p4,{i:len(e.qualifying_ii_edge_ids) for i,e in e4.items()},
                   {i:e.typed_cycle_count for i,e in e4.items()},4),
            Result('c5-max',c5,s5,r5,p5,{i:e.qualifying_ii_edges for i,e in e5.items()},
                   {i:e.typed_cycle_count for i,e in e5.items()},5)]
        if include_c6:
            e6=_c6_evidence(user=u,observed=observed,user_items=user_items,item_users=item_users,
                           item_neighbors=item_neighbors,c6_truss=weights[6])
            c6=set(e6);s6={i:float(e.max_value) for i,e in e6.items()}
            r6,p6=ranked(c6,s6,pad=False)
            rs.append(Result('c6-max',c6,s6,r6,p6,{i:len(e.qualifying_ii_edge_ids) for i,e in e6.items()},
                             {i:e.typed_cycle_count for i,e in e6.items()},6))
        sk=_itemknn_positive_scores(observed=observed,user_items=user_items,item_users=item_users,
                                   inverse_sqrt_item_users=inv)
        ck=set(catalog)-observed
        rk=_itemknn_ranking(observed=observed,positive_scores=sk,catalog_by_degree=by_degree,
                           degree=degree,target_length=cutoff)
        rs.append(Result('itemknn-full-catalog',ck,{i:sk.get(i,0.0) for i in ck},rk,
                         {i for i in rk if i not in sk}))
        common={'selected_rank':selected_rank,'global_training_degree_rank':global_rank[u],
                'user_node':u,'user_id':case.nodes[u].entity_id,'training_item_degree':len(observed)}
        us={**common,'ground_truth_all':len(truth)}
        for result in rs:
            if set(result.ranking)&observed: raise ValueError('Recommending an observed item')
            if not set(result.ranking)<=set(catalog):raise ValueError('Non-catalog recommendation')
            m=metrics(result.ranking,truth,result.candidates,result.padding,cutoff)
            metric_rows.append({**common,'method':result.method,'cutoff':cutoff,**m})
            key=result.method.replace('-','_')
            us[f'{key}_candidate_count']=len(result.candidates)
            us[f'{key}_candidate_ground_truth']=len(result.candidates&truth)
            for rank,i in enumerate(result.ranking[:cutoff],1):
                ranking_rows.append({k:v for k,v in {**common,'method':result.method,'rank':rank,
                    'item_node':i,'item_id':case.nodes[i].entity_id,'title':case.nodes[i].title,
                    'score':result.scores.get(i,0.0),'item_full_graph_degree':degree[i],
                    'structural_candidate':int(i in result.candidates),'padding_item':int(i in result.padding),
                    'qualifying_ii_edges':result.edge_counts.get(i,0),
                    'typed_c4_count':result.cycle_counts.get(i,0) if result.length==4 else 0,
                    'typed_c5_count':result.cycle_counts.get(i,0) if result.length==5 else 0,
                    'typed_c6_count':result.cycle_counts.get(i,0) if result.length==6 else 0,
                    'ground_truth':int(i in truth)}.items() if k!='training_item_degree'})
        user_rows.append(us)
        if selected_rank%10==0 or selected_rank==len(selected):
            print(f'[table6-eval] users={selected_rank}/{len(selected)} '
                  f'elapsed_seconds={time.monotonic()-start:.1f}',file=sys.stderr,flush=True)
    write_tsv(out/'selected_users.tsv',list(user_rows[0]),user_rows)
    write_tsv(out/'per_user_metrics.tsv',list(metric_rows[0]),metric_rows)
    ranking_fields=['selected_rank','global_training_degree_rank','user_node','user_id','method','rank',
        'item_node','item_id','title','score','item_full_graph_degree','structural_candidate','padding_item',
        'qualifying_ii_edges','typed_c4_count','typed_c5_count','typed_c6_count','ground_truth']
    write_tsv(out/'rankings_top.tsv',ranking_fields,ranking_rows)
    aggregate=[]
    for method in methods:
        rows=[r for r in metric_rows if r['method']==method];n=len(rows)
        total_hits=sum(r['hits'] for r in rows);gt=sum(r['ground_truth_all'] for r in rows)
        item={'method':method,'cutoff':cutoff,'users':n,
             'users_with_hit':sum(r['hits']>0 for r in rows),
             'zero_structural_candidate_users':sum(r['structural_candidate_count']==0 for r in rows),
             'users_with_padding':sum(r['padding_hits']>0 for r in rows),
             'average_structural_candidate_count':fmean(r['structural_candidate_count'] for r in rows),
             'average_candidate_ground_truth':fmean(r['candidate_ground_truth'] for r in rows),
             'average_ground_truth_all':fmean(r['ground_truth_all'] for r in rows),
             'average_recommendation_count':fmean(r['recommendation_count'] for r in rows),
             'total_hits':total_hits,'total_structural_hits':sum(r['structural_hits'] for r in rows),
             'total_padding_hits':sum(r['padding_hits'] for r in rows),
             'average_precision':fmean(r['precision'] for r in rows),
             'micro_precision':total_hits/(n*cutoff),
             'average_hr':fmean(r['hr'] for r in rows),
             'average_recall':fmean(r['recall'] for r in rows),
             'micro_recall':total_hits/gt if gt else 0.0,
             'average_ndcg':fmean(r['ndcg'] for r in rows)}
        # Count actual padded lists rather than only padded HITS.
        item['users_with_padding']=len({r['user_id'] for r in ranking_rows
                                       if r['method']==method and r['padding_item']==1})
        aggregate.append(item)
    write_tsv(out/'aggregate.tsv',list(aggregate[0]),aggregate)
    binding={name:sha256_file(case.root/name) for name in ('graph.txt','nodes.tsv','edges.tsv','test_edges.tsv','case.json')}
    value_hashes={f'c{l}_truss.txt':sha256_file(case.root/'values'/f'c{l}_truss.txt') for l in lengths}
    meta={'protocol_version':'table6-final-padded-v1','case':str(case.root),'graph_sha256':case.graph_sha256,
          'case_file_sha256':binding,'value_sha256':value_hashes,'methods':methods,
          'top_users_requested':top_users,'eligible_users':len(eligible),'selected_users':len(selected),
          'cutoff':cutoff,'python_version':sys.version,'platform':platform.platform(),
          'evaluation_seconds':time.monotonic()-start,
          'score_tie_break':'score DESC, full mixed-graph degree DESC, local numeric node ID ASC',
          'padding':'C3/C4-typed/C5-max: structural candidates first, then other unobserved warm items by degree DESC and node ID ASC',
          'c6_padding':False,'precision':'hits / requested K (fixed)',
          'hr':'1 if at least one hit else 0',
          'ndcg':'binary DCG / IDCG using min(K, ALL warm-new test GT count); not candidate-only IDCG',
          'macro_denominator':'all selected users, including zero structural-candidate users',
          'c6_status':c6_status or {'status':'COMPLETED' if include_c6 else 'NOT_RUN'},
          'output_file_sha256':{name:sha256_file(out/name) for name in ('aggregate.tsv','rankings_top.tsv','selected_users.tsv','per_user_metrics.tsv')}}
    _json(out/'evaluation.json',meta)
    lines=[f'===== FULL-GRAPH TABLE-6 @ {cutoff} =====',f'selected_users_with_test_gt: {len(selected)}',
           f'eligible_users_total: {len(eligible)}',f'case: {case.root}',f'graph_sha256: {case.graph_sha256}',
           f'Precision@{cutoff} uses fixed denominator {cutoff}; HR@{cutoff} is binary hit-any.']
    for r in aggregate:
        lines.append(f"{r['method']:>24s}@{cutoff} avg_candidates={r['average_structural_candidate_count']:.4f} "
                     f"avg_candidate_gt={r['average_candidate_ground_truth']:.4f} "
                     f"avg_precision={r['average_precision']:.8f} avg_hr={r['average_hr']:.8f} "
                     f"avg_ndcg={r['average_ndcg']:.8f} total_hits={r['total_hits']}")
    lines.append(f'aggregate: {out / "aggregate.tsv"}')
    text='\n'.join(lines)+'\n';atomic_write_text(out/'aggregate.pretty.log',text); print(text,end='',flush=True)
    # All top-K rows are preserved in a separate readable LOG, not just in TSV.
    with (out/'rankings.log').open('w',encoding='utf-8') as f:
        for r in ranking_rows:
            f.write(f"user={r['user_id']} method={r['method']} rank={r['rank']} score={r['score']} "
                    f"degree={r['item_full_graph_degree']} padding={r['padding_item']} "
                    f"{'HIT' if r['ground_truth'] else '-'} item={r['item_id']} title={r['title']}\n")
    render_table(out,aggregate,cutoff,meta['c6_status'])
    return out/'aggregate.tsv'


def render_table(out:Path, aggregate:list[dict], cutoff:int, c6_status:dict) -> None:
    by={r['method']:r for r in aggregate}
    order=[('itemknn-full-catalog','ItemKNN'),('c3-truss-direct','C3-truss'),('c4-truss-typed','C4-truss'),
           ('c5-max','C5-truss'),('c6-max','C6-truss')]
    lines=[f'Method\tNDCG@{cutoff}\tPrecision@{cutoff}\tStatus']
    tex=[r'\begin{tabular}{lrr}',r'\toprule',f'Method & NDCG@{cutoff} & Precision@{cutoff} '+r'\\',r'\midrule']
    for method,label in order:
        r=by.get(method)
        if r is None:
            state=c6_status.get('status','NOT_RUN')
            a=b='INF' if state=='TIMEOUT' else '--'
        else:
            state='COMPLETED';a=f"{r['average_ndcg']:.4f}";b=f"{r['average_precision']:.4f}"
        lines.append(f'{label}\t{a}\t{b}\t{state}')
        texlabel='ItemKNN' if label=='ItemKNN' else '$C_'+label[1]+'$-truss'
        tex.append(f'{texlabel} & {a} & {b} '+r'\\')
    tex.extend([r'\bottomrule',r'\end{tabular}',
        '% INF appears ONLY after a recorded TIMEOUT; it is not a numeric metric.',
        '% State the time budget in the caption. -- means not run or unavailable.',
        '% Requires the booktabs package.'])
    atomic_write_text(out/'table6.tsv','\n'.join(lines)+'\n')
    atomic_write_text(out/'table6.tex','\n'.join(tex)+'\n')


def verify_reference(results: Path, reference:Path) -> bool:
    """Compare computed metrics to the user's supplied rounded numbers. Never alter them."""
    out=Path(results);ref=json.loads(Path(reference).read_text())
    with (out/'aggregate.tsv').open(encoding='utf-8',newline='') as f:
        actual={r['method']:r for r in csv.DictReader(f,delimiter='\t')}
    problems=[];checks=[]
    for method, expected in ref['methods'].items():
        row=actual.get(method)
        if row is None: problems.append(f'{method}: missing');continue
        for field,want in {**expected,'cutoff':ref['cutoff'],'users':ref['users']}.items():
            if field.startswith('average_'):
                tol=0.0000501 if field in ('average_structural_candidate_count','average_candidate_ground_truth') else 0.0000000051
                got=float(row[field]);ok=abs(got-want)<=tol
            else: got=int(row[field]);ok=got==want
            checks.append(dict(method=method,field=field,computed=got,reference=want,match=ok))
            if not ok:problems.append(f'{method} {field}: computed={got}, reference={want}')
    info={'reference_provenance':ref['provenance'],'passed':not problems,'checks':checks,'differences':problems}
    _json(out/'reference_check.json',info)
    text=('PASS: four measured methods match the supplied reference within displayed rounding.\n'
          if not problems else 'MISMATCH (computed outputs were NOT modified):\n'+'\n'.join(problems)+'\n')
    atomic_write_text(out/'reference_check.log',text);print(text,end='')
    return not problems

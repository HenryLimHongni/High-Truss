from __future__ import annotations
import csv
import contextlib
import io
import itertools
import json
import math
import random
import tempfile
import unittest
from collections import Counter,defaultdict
from pathlib import Path

from mlrepro.artifacts import NodeRecord,EdgeRecord,write_case,load_case,load_value_file
from mlrepro.dataset import BuildOptions,Movie,_choose_cutoff,_movie_similarity,build_content_item_edges,build_case
from mlrepro.decompose import compute,validate_values,BackendTimeout
from mlrepro.evidence import (_c3_direct_scores,c4_typed_evidence,_c5_evidence,_c6_evidence,
                            _graph_relations,_rank_with_optional_padding,_itemknn_positive_scores)
from mlrepro.evaluate import evaluate,metrics,render_table,verify_reference
from mlrepro.cli import import_case


def make_case(path, n, pairs, types=None, truth=()):
    types=types or ['item']*n
    keys=[('U:' if types[i]=='user' else 'I:')+str(i) for i in range(n)]
    nodes={keys[i]:NodeRecord(keys[i],types[i],str(i),f'Node {i}') for i in range(n)}
    edges={tuple(sorted((keys[a],keys[b]))):EdgeRecord('UI' if types[a]!=types[b] else 'II') for a,b in pairs}
    return write_case(output_dir=path,node_records=nodes,edge_records=edges,
                      truth=[(keys[u],keys[i]) for u,i in truth],metadata={'dataset':'synthetic unit test'})


def enumerate_cycles(edges,length):
    adj=defaultdict(set)
    for a,b in edges:adj[a].add(b);adj[b].add(a)
    found=[]
    for start in sorted(adj):
        path=[start]
        def dfs():
            if len(path)==length:
                if start in adj[path[-1]] and path[1]<path[-1]:
                    found.append(tuple(tuple(sorted((path[j],path[(j+1)%length]))) for j in range(length)))
                return
            for w in sorted(adj[path[-1]]):
                if w>start and w not in path:
                    path.append(w);dfs();path.pop()
        dfs()
    return found


def oracle(edges,length):
    support=Counter(e for cyc in enumerate_cycles(edges,length) for e in cyc)
    truss=dict.fromkeys(edges,0)
    for k in range(1,max(support.values(),default=0)+1):
        alive=set(edges)
        while True:
            counts=Counter(e for cyc in enumerate_cycles(alive,length) for e in cyc)
            remove={e for e in alive if counts[e]<k}
            if not remove:break
            alive-=remove
        for e in alive:truss[e]=k
    return [support[e] for e in edges],[truss[e] for e in edges]


class BackendTests(unittest.TestCase):
    def test_simple_cycles_and_clique(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for length in (3,4,5,6):
                pairs={tuple(sorted((i,(i+1)%length))) for i in range(length)}
                case=make_case(root/f'C{length}',length,pairs)
                compute(case.root,length,timeout=10)
                s,t=validate_values(case,length)
                self.assertEqual(s,(1,)*length);self.assertEqual(t,(1,)*length)
            case=make_case(root/'K6',6,set(itertools.combinations(range(6),2)))
            for length in (3,4,5,6):
                compute(case.root,length,timeout=10)
                s,t=validate_values(case,length)
                per_edge=math.factorial(4)//math.factorial(6-length)
                self.assertEqual(s,(per_edge,)*15);self.assertEqual(t,s)

    def test_random_against_independent_fixed_point(self):
        rng=random.Random(20261004)
        with tempfile.TemporaryDirectory() as td:
            for trial in range(16):
                n=5+trial%3;p=0.18+0.65*rng.random()
                pairs={e for e in itertools.combinations(range(n),2) if rng.random()<p} or {(0,1)}
                case=make_case(Path(td)/str(trial),n,pairs)
                local=[(case.node_to_id[a],case.node_to_id[b]) for a,b in case.edges]
                for length in (3,4,5,6):
                    compute(case.root,length,timeout=15)
                    actual=validate_values(case,length)
                    expect=oracle(local,length)
                    self.assertEqual(actual,tuple(tuple(a) for a in expect),(trial,length))

    def test_no_cycle_and_value_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            case=make_case(Path(td)/'case',4,{(0,1),(1,2),(2,3)})
            for length in (3,4,5,6):
                compute(case.root,length,timeout=10)
                self.assertEqual(validate_values(case,length),((0,0,0),(0,0,0)))
            p=case.root/'values/c5_truss.txt';p.write_text('1\n0\n0\n')
            with self.assertRaises(ValueError):validate_values(case,5)

    def test_timeout_has_no_fabricated_values(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);case=make_case(root/'case',6,set(itertools.combinations(range(6),2)))
            binaries=root/'bin';binaries.mkdir()
            fake=binaries/'streaming_c6_truss';fake.write_text('#!/bin/sh\nsleep 30\n');fake.chmod(0o755)
            with self.assertRaises(BackendTimeout):compute(case.root,6,timeout=0.1,bin_dir=binaries)
            status=json.loads((case.root/'values/c6_status.json').read_text())
            self.assertEqual(status['status'],'TIMEOUT')
            self.assertFalse((case.root/'values/c6_truss.txt').exists())
            self.assertIn('TIMEOUT',(case.root/'values/c6_backend.log').read_text())


class ProtocolTests(unittest.TestCase):
    def test_similarity_weights_and_strict_threshold(self):
        a=Movie('1','A (2000)',2000,frozenset({'Action'}))
        b=Movie('2','B (2000)',2000,frozenset({'Drama'}))
        s,_=_movie_similarity(a,b,genre_weight=0.8,year_weight=0.2)
        self.assertEqual(s,0.2)
        c=Movie('3','C (2010)',2010,frozenset({'Action'}))
        self.assertAlmostEqual(_movie_similarity(a,c,genre_weight=0.8,year_weight=0.2)[0],0.9)
        self.assertFalse(build_content_item_edges({'1':a,'2':b},{'1','2'},top_k=20,
                      genre_weight=0.8,year_weight=0.2,similarity_threshold=s))

    def test_topk_is_directed_selection_then_union(self):
        movies={str(i):Movie(str(i),str(i),2000,frozenset({'Drama'})) for i in range(1,5)}
        edges=build_content_item_edges(movies,set(movies),top_k=1,genre_weight=0.8,year_weight=0.2,similarity_threshold=0.4)
        self.assertEqual(set(edges),{('1','2'),('1','3'),('1','4')})
        # Undirected degree can exceed the per-item selection cap.
        self.assertEqual(sum('1' in e for e in edges),3)

    def test_global_quantile_ties_and_train_test_disjointness(self):
        self.assertEqual(_choose_cutoff([1,2,3,4,5,6,7,8,8,9],BuildOptions()),8)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);ratings=root/'u.data';movies=root/'u.item'
            rows=[('1','1',1,1),('1','2',5,2),('2','1',3,3),('2','3',2,4),
                  ('1','1',4,8),('1','3',4,9),('3','1',4,9),('2','4',4,9)]
            ratings.write_text(''.join(f'{u}\t{i}\t{r}\t{t}\n' for u,i,r,t in rows))
            movies.write_text(''.join('|'.join([str(i),f'Movie {i} (2000)','01-Jan-2000','','url']+
                                    ['0','1']+['0']*17)+'\n' for i in range(1,5)),encoding='latin-1')
            case=build_case(ratings_path=ratings,movies_path=movies,output_dir=root/'case',
                            options=BuildOptions(fixed_cutoff=8))
            self.assertEqual(set(case.truth),{('U:1','I:3')})
            self.assertEqual(case.metadata['training_pairs'],4)
            self.assertNotIn('U:3',case.node_to_id);self.assertNotIn('I:4',case.node_to_id)
            self.assertIn(('U:1','I:1'),case.edges) # 1-star input remains an interaction

    def test_c3_uses_ii_value_only_and_keeps_zero(self):
        c,s,n=_c3_direct_scores(observed={1,2},item_neighbors={1:[(3,0),(4,2)],2:[(3,1)]},c3_truss=(2,7,0))
        self.assertEqual(c,{3,4});self.assertEqual(s,{3:7.0,4:0.0});self.assertEqual(n[3],2)

    def test_c4_typed_requires_two_distinct_anchors(self):
        ev=c4_typed_evidence(observed={1,2,3},item_neighbors={1:[(4,0),(5,3)],2:[(4,1)],3:[(4,2)]},
                             c4_truss=(2,7,4,8))
        self.assertEqual(set(ev),{4});self.assertEqual(ev[4].max_value,7)
        self.assertEqual(ev[4].typed_cycle_count,3)

    def test_padding_degree_tie_break_and_fixed_denominator(self):
        ranking,padding=_rank_with_optional_padding(structural_candidates={1},scores={1:3},observed={0},
                      catalog_by_degree=(4,3,2,1,0),degree=[0,1,5,8,9],target_length=5,pad=True)
        self.assertEqual(ranking,[1,4,3,2]);self.assertEqual(padding,{4,3,2})
        m=metrics(ranking,{4,2},{1},padding,5)
        self.assertEqual(m['precision'],2/5);self.assertEqual(m['hr'],1)
        self.assertEqual(m['padding_hits'],2);self.assertEqual(m['candidate_ground_truth'],0)
        # IDCG uses all test positives, not just positives in structural candidates.
        dcg=1/math.log2(3)+1/math.log2(5)
        self.assertAlmostEqual(m['ndcg'],dcg/(1+1/math.log2(3)))
        r,p=_rank_with_optional_padding(structural_candidates={1,2},scores={1:7,2:7},observed={0},
                        catalog_by_degree=(2,1),degree=[0,3,10],target_length=2,pad=True)
        self.assertEqual(r,[2,1])
        self.assertEqual(metrics([], {1},set(),set(),5)['precision'],0)

    def test_c5_typed_matches_independent_cycle_enumeration(self):
        rng=random.Random(79)
        for trial in range(25):
            nu,ni=3,5;users=list(range(nu));items=list(range(nu,nu+ni))
            ui={(u,i) for u in users for i in items if rng.random()<0.5}
            ii={e for e in itertools.combinations(items,2) if rng.random()<0.65}
            edges=sorted(ui|ii);values=tuple(rng.randrange(1,30) for e in edges)
            uid=defaultdict(set);iid=defaultdict(set);inn=defaultdict(list)
            for eid,(a,b) in enumerate(edges):
                if a<nu:uid[a].add(b);iid[b].add(a)
                else:inn[a].append((b,eid));inn[b].append((a,eid))
            observed=uid[0]
            actual=_c5_evidence(user=0,observed=observed,user_items=uid,item_users=iid,
                                item_neighbors=inn,c5_truss=values)
            expected=defaultdict(list)
            for cyc in enumerate_cycles(edges,5):
                nodes=set(x for e in cyc for x in e)
                if 0 not in nodes or sum(x<nu for x in nodes)!=2:continue
                iet=[e for e in cyc if e[0]>=nu]
                if len(iet)!=1:continue
                for c in iet[0]:
                    if c not in observed:
                        expected[c].append(values[edges.index(iet[0])])
            self.assertEqual(set(actual),set(expected))
            for c,v in expected.items():
                self.assertEqual(actual[c].typed_cycle_count,len(v))
                self.assertEqual(actual[c].cycle_value_sum,sum(v))
                self.assertEqual(actual[c].max_value,max(v))

    def test_c6_typed_one_cycle_two_targets(self):
        # u=0,v=1; u-a-b-v-c-d-u, a=2,b=3,c=4,d=5.
        ui={0:{2,5},1:{3,4}};iu={2:{0},3:{1},4:{1},5:{0}}
        ev=_c6_evidence(user=0,observed=ui[0],user_items=ui,item_users=iu,
                       item_neighbors={2:[(3,0)],3:[(2,0)],4:[(5,1)],5:[(4,1)]},c6_truss=(9,6))
        self.assertEqual(set(ev),{3,4});self.assertEqual(ev[3].max_value,9);self.assertEqual(ev[4].max_value,6)
        self.assertEqual(ev[3].typed_cycle_count,1);self.assertEqual(ev[4].typed_cycle_count,1)

    def test_itemknn_full_catalog_score(self):
        ui={0:{2,3},1:{2,4},5:{3,4}};iu={2:{0,1},3:{0,5},4:{1,5}}
        s=_itemknn_positive_scores(observed=ui[0],user_items=ui,item_users=iu,
                                   inverse_sqrt_item_users={i:1/math.sqrt(len(us)) for i,us in iu.items()})
        self.assertAlmostEqual(s[4],1)

    def test_end_to_end_import_and_no_avg_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            case=make_case(root/'case',9,{(0,2),(0,3),(1,2),(1,4),(1,5),(1,6),(1,7),(1,8),
                                        (3,4),(2,4),(2,5),(3,5),(5,6)},
                           ['user','user']+['item']*7,[(0,4),(0,6)])
            for l in (3,4,5):compute(case.root,l,timeout=10)
            out=root/'results';evaluate(case.root,out,cutoff=5)
            with (out/'aggregate.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            self.assertEqual({r['method'] for r in rows},
                {'c3-truss-direct','c4-truss-typed','c5-max','itemknn-full-catalog'})
            self.assertTrue(all(int(r['users'])==1 for r in rows))
            with (out/'rankings_top.tsv').open() as f:rank=list(csv.DictReader(f,delimiter='\t'))
            self.assertTrue(all(sum(r['method']==m for r in rank)==5 for m in {r['method'] for r in rank}))
            self.assertTrue(any(r['method']=='c4-truss-typed' and r['padding_item']=='1' for r in rank))
            self.assertIn('NOT_RUN',(out/'table6.tsv').read_text())
            self.assertNotIn('INF',(out/'table6.tsv').read_text())
            imported=root/'independent';import_case(case.root,imported)
            self.assertEqual(load_case(imported).graph_sha256,case.graph_sha256)
            for l in (3,4,5):self.assertEqual(validate_values(load_case(imported),l),validate_values(case,l))
            with self.assertRaises(FileExistsError):import_case(case.root,imported)
            render_table(out,[],5,{'status':'TIMEOUT','timeout_seconds':0.1})
            self.assertIn('C6-truss\tINF\tINF\tTIMEOUT',(out/'table6.tsv').read_text())

    def test_reference_check_reports_failure_without_changing_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);agg=root/'aggregate.tsv';agg.write_text('method\tusers\tcutoff\ttotal_hits\nM\t1\t5\t0\n')
            ref=root/'ref.json';ref.write_text(json.dumps({'provenance':'synthetic test','users':1,'cutoff':5,
                                                          'methods':{'M':{'total_hits':2}}}))
            original=agg.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(verify_reference(root,ref))
            self.assertEqual(original,agg.read_bytes())



class EmptyCandidateTests(unittest.TestCase):
    def test_empty_c4_c5_candidates_are_padded_and_user_is_not_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            # Pivot 0 observed only item 2: no simple typed C4/C5 is possible.
            # Item 3 is the high-degree unseen ground truth, so padding can hit.
            case=make_case(root/'case',8,{(0,2),(1,3),(1,4),(1,5),(1,6),(1,7),(2,3)},
                           ['user','user']+['item']*6,[(0,3)])
            for l in (3,4,5):compute(case.root,l,timeout=10)
            out=root/'out';evaluate(case.root,out,cutoff=5)
            with (out/'aggregate.tsv').open() as f:
                rows={r['method']:r for r in csv.DictReader(f,delimiter='\t')}
            for m in ('c4-truss-typed','c5-max'):
                self.assertEqual(int(rows[m]['users']),1)
                self.assertEqual(float(rows[m]['average_structural_candidate_count']),0)
                self.assertEqual(float(rows[m]['average_candidate_ground_truth']),0)
                self.assertEqual(float(rows[m]['average_recommendation_count']),5)
                self.assertEqual(int(rows[m]['total_hits']),1)
                self.assertEqual(int(rows[m]['total_padding_hits']),1)
                self.assertEqual(float(rows[m]['average_precision']),0.2)

if __name__=='__main__':unittest.main()

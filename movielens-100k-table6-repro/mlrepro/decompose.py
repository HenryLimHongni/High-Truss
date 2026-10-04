"""Exact ordinary simple-cycle decomposition, streamed logs and safe cache reuse."""
from __future__ import annotations

import csv
import fcntl
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .artifacts import CaseArtifact, load_case, load_value_file, write_value_file
from .utils import atomic_write_text, sha256_file

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'bin'

class BackendTimeout(RuntimeError):
    pass


def validate_values(case: CaseArtifact, length: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Verify checksums, graph binding, row counts, and basic counting invariants."""
    folder = case.root / 'values'
    support = load_value_file(case, folder / f'c{length}_support.txt', expected_algorithm=f'c{length}_support')
    truss = load_value_file(case, folder / f'c{length}_truss.txt', expected_algorithm=f'c{length}_truss')
    if any(t > s for s, t in zip(support, truss)):
        raise ValueError(f'C{length}: trussness exceeds initial support')
    if sum(support) % length:
        raise ValueError(f'C{length}: sum of edge supports is not divisible by {length}')
    if any((s > 0) != (t > 0) for s, t in zip(support, truss)):
        raise ValueError(f'C{length}: positive support and positive trussness disagree')
    return support, truss


def _parse(case: CaseArtifact, path: Path) -> tuple[tuple[int, ...], tuple[int, ...]]:
    expected = [(case.node_to_id[a], case.node_to_id[b]) for a,b in case.edges]
    support, truss = [0]*case.edge_count, [0]*case.edge_count
    seen = set()
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if not {'edge_id','u','v','initial_support','trussness'} <= set(reader.fieldnames or ()):
            raise ValueError('Invalid backend output columns')
        for row in reader:
            e = int(row['edge_id'])
            if not 0 <= e < case.edge_count or e in seen:
                raise ValueError(f'Invalid/duplicate edge ID {e}')
            if tuple(sorted((int(row['u']),int(row['v'])))) != expected[e]:
                raise ValueError(f'Backend endpoint mismatch at edge {e}')
            s,t = int(row['initial_support']),int(row['trussness'])
            if not 0 <= t <= s:
                raise ValueError(f'Invalid support/truss at edge {e}')
            support[e],truss[e]=s,t
            seen.add(e)
    if len(seen) != case.edge_count:
        raise ValueError('Backend output is incomplete')
    return tuple(support),tuple(truss)


def compute(case_dir: Path, length: int, *, timeout: float | None = None, force: bool = False,
            bin_dir: Path = BIN) -> dict:
    if length not in (3,4,5,6):
        raise ValueError('cycle length must be 3,4,5,6')
    if timeout is not None and timeout <= 0:
        raise ValueError('timeout must be positive')
    case=load_case(case_dir)
    folder=case.root/'values'; folder.mkdir(parents=True,exist_ok=True)
    status_path=folder/f'c{length}_status.json'
    log_path=folder/f'c{length}_backend.log'
    with (folder/f'.c{length}.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f'C{length} already running for this case; do not launch a second writer') from exc
        if not force:
            try:
                support,truss=validate_values(case,length)
            except (FileNotFoundError,ValueError):
                pass
            else:
                info={'status':'CACHED','length':length,'graph_sha256':case.graph_sha256,
                      'support_sha256':sha256_file(folder/f'c{length}_support.txt'),
                      'truss_sha256':sha256_file(folder/f'c{length}_truss.txt')}
                atomic_write_text(status_path,json.dumps(info,indent=2)+'\n')
                print(f'[resume] C{length} support/truss verified',flush=True)
                return info
        name={3:'short_cycle_truss',4:'short_cycle_truss',5:'c5_truss_sparse',6:'streaming_c6_truss'}[length]
        executable=(Path(bin_dir)/name).resolve()
        if not executable.is_file() or not os.access(executable,os.X_OK):
            raise FileNotFoundError(f'Backend missing: {executable}; run bash scripts/bootstrap.sh')
        started=time.monotonic()
        info={'status':'RUNNING','length':length,'graph_sha256':case.graph_sha256,
              'timeout_seconds':timeout,'backend':name,'log_path':str(log_path),
              'started_at_unix':time.time(),'pid':os.getpid(),
              'slurm_job_id':os.environ.get('SLURM_JOB_ID')}
        with tempfile.TemporaryDirectory(prefix=f'c{length}-',dir=folder) as temporary:
            result=Path(temporary)/'result.tsv'
            command=[str(executable)]
            if length in (3,4): command+=['--length',str(length)]
            command += [str(case.graph_path),str(result)]
            info['command']=command
            atomic_write_text(status_path,json.dumps(info,indent=2)+'\n')
            print(f'[C{length}] starting; live backend log: {log_path}',flush=True)
            with log_path.open('w',encoding='utf-8') as log:
                log.write(json.dumps(info,indent=2)+'\n\n--- live stdout/stderr ---\n');log.flush()
                process=None
                try:
                    process=subprocess.Popen(command,cwd=temporary,stdout=log,stderr=subprocess.STDOUT,
                                             start_new_session=True)
                    code=process.wait(timeout=timeout)
                    info['elapsed_seconds']=time.monotonic()-started
                    info['returncode']=code
                    if code != 0:
                        raise RuntimeError(f'C{length} backend exited {code}; see {log_path}')
                    support,truss=_parse(case,result)
                    if sum(support)%length or any((s>0)!=(t>0) for s,t in zip(support,truss)):
                        raise ValueError(f'C{length} output failed counting invariants')
                    if sha256_file(case.graph_path) != case.graph_sha256:
                        raise RuntimeError('Input graph changed during decomposition')
                    # Values are published only after a successful complete backend result.
                    write_value_file(case,support,output=folder/f'c{length}_support.txt',
                                     algorithm=f'c{length}_support',backend=name,
                                     elapsed_seconds=info['elapsed_seconds'])
                    write_value_file(case,truss,output=folder/f'c{length}_truss.txt',
                                     algorithm=f'c{length}_truss',backend=name,
                                     elapsed_seconds=info['elapsed_seconds'])
                    validate_values(case,length)
                    info.update(status='COMPLETED',edges=case.edge_count,
                                cycles=sum(support)//length,maximum_truss=max(truss,default=0),
                                positive_truss_edges=sum(t>0 for t in truss))
                except subprocess.TimeoutExpired as exc:
                    if process is not None and process.poll() is None:
                        os.killpg(process.pid,signal.SIGKILL); process.wait()
                    info.update(status='TIMEOUT',elapsed_seconds=time.monotonic()-started)
                    raise BackendTimeout(f'C{length} exceeded {timeout}s; see {log_path}') from exc
                except BaseException as exc:
                    if process is not None and process.poll() is None:
                        os.killpg(process.pid,signal.SIGKILL); process.wait()
                    info.update(status='INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else 'FAILED',
                                elapsed_seconds=time.monotonic()-started,error=str(exc))
                    raise
                finally:
                    log.write('\n--- final status ---\n'+json.dumps(info,indent=2)+'\n');log.flush()
                    atomic_write_text(status_path,json.dumps(info,indent=2)+'\n')
        print(f'[C{length}] completed edges={case.edge_count} max_truss={info["maximum_truss"]} '
              f'elapsed_seconds={info["elapsed_seconds"]:.3f}',flush=True)
        return info

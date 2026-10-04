"""Official MovieLens-100K downloader. No third-party Python dependencies."""
from __future__ import annotations
import hashlib
import json
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from .utils import atomic_write_text, sha256_file

URL='https://files.grouplens.org/datasets/movielens/ml-100k.zip'
# Same official archive pin as the supplied final project.
ARCHIVE_MD5='0e33842e24a9c977be4e0107933c0723'

def _verify(p:Path) -> None:
    h=hashlib.md5()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    if h.hexdigest()!=ARCHIVE_MD5:
        raise ValueError(f'Archive checksum mismatch: {p}; expected MD5 {ARCHIVE_MD5}')
    with zipfile.ZipFile(p) as z:
        if z.testzip() is not None:raise ValueError('Damaged ZIP archive')
        for name in ('ml-100k/u.data','ml-100k/u.item','ml-100k/README'):
            if name not in z.namelist():raise ValueError(f'Archive missing {name}')

def download(data_dir:Path, archive:Path|None=None) -> tuple[Path,Path]:
    data_dir=Path(data_dir).resolve();data_dir.mkdir(parents=True,exist_ok=True)
    target=data_dir/'ml-100k.zip'
    if archive is not None:
        source=Path(archive).resolve();_verify(source)
        if source!=target:shutil.copy2(source,target)
    elif not target.is_file():
        error=None
        for attempt in range(3):
            try:
                with tempfile.NamedTemporaryFile(prefix='ml100k-',suffix='.part',dir=data_dir,delete=False) as f:
                    partial=Path(f.name)
                    request=urllib.request.Request(URL,headers={'User-Agent':'MovieLens-Table6-Repro/1.0'})
                    with urllib.request.urlopen(request,timeout=60) as response:
                        shutil.copyfileobj(response,f)
                _verify(partial);partial.replace(target);error=None;break
            except Exception as exc:
                error=exc
                if 'partial' in locals():partial.unlink(missing_ok=True)
                if attempt<2:time.sleep(2*(attempt+1))
        if error:
            raise RuntimeError('Official download failed. Copy an existing ml-100k.zip into '
                               f'{target}, or use --archive PATH. Original error: {error}') from error
    _verify(target)
    with zipfile.ZipFile(target) as z:
        for name in ('ml-100k/u.data','ml-100k/u.item','ml-100k/README'):
            output=data_dir/name;output.parent.mkdir(parents=True,exist_ok=True)
            raw=z.read(name)
            if not output.exists() or output.read_bytes()!=raw:
                with tempfile.NamedTemporaryFile(dir=output.parent,delete=False) as f:
                    tmp=Path(f.name);f.write(raw)
                tmp.replace(output)
    ratings=data_dir/'ml-100k/u.data';movies=data_dir/'ml-100k/u.item'
    meta={'source':URL,'archive_md5':ARCHIVE_MD5,'archive_sha256':sha256_file(target),
          'ratings_sha256':sha256_file(ratings),'movies_sha256':sha256_file(movies)}
    atomic_write_text(data_dir/'source.json',json.dumps(meta,indent=2)+'\n')
    print(f'[data] verified official archive; ratings={ratings}; movies={movies}',flush=True)
    return ratings,movies

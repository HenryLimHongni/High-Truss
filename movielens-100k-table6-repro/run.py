#!/usr/bin/env python3
import sys
if sys.version_info < (3,10):
    raise SystemExit('Python >=3.10 required. Set CYCLECASE_PYTHON to your Python 3.11 executable.')
from mlrepro.cli import main
if __name__=='__main__':
    raise SystemExit(main())

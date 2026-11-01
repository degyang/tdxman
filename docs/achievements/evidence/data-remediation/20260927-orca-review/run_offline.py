"""Reproduce the review suite using this worktree's interpreter and isolated pools."""

import os
import sys
from pathlib import Path

import pytest

LAB = Path('/home/ubuntu/aspool-labs/20260927-orca-review/task_e156082ba705')
PROTECTED = (Path('/home/ubuntu/.aspool'), Path('/home/ubuntu/aspool-recovery'))


def audit(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('Offline review blocks external connections')
    paths = []
    if event == 'open':
        path, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            paths = [path]
    elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod'):
        paths = [args[0]]
    elif event in ('os.rename', 'os.link', 'os.symlink'):
        paths = list(args[:2])
    for path in paths:
        if isinstance(path, (str, bytes, os.PathLike)):
            resolved = Path(os.fsdecode(path)).resolve()
            if any(resolved == root or root in resolved.parents for root in PROTECTED):
                raise RuntimeError(f'Review blocks production/recovery mutation: {event}')


if __name__ == '__main__':
    os.environ['XMTDX_LIVE'] = '0'
    sys.addaudithook(audit)
    arguments = sys.argv[1:] or ['tests/', '-q', '--basetemp=' + str(LAB / 'full-final')]
    raise SystemExit(pytest.main(arguments))

"""App Base is a copy (src/base), made by App Base's update-base.ps1, which writes VERSION with each file's SHA-256.
The copy isn't edited here: a fix goes into App Base, and a new copy is made from it."""
import hashlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'src' / 'base'


def recorded():
    lines = (BASE / 'VERSION').read_text(encoding='utf-8').splitlines()
    return {name: digest for digest, name in (line.split('  ', 1) for line in lines if '  ' in line)}


def present():
    return {str(p.relative_to(BASE)).replace('\\', '/'): p for p in BASE.rglob('*')
            if p.is_file() and p.name != 'VERSION' and '__pycache__' not in p.parts}


def test_the_copy_is_unchanged():
    files = present()
    changed = [name for name, digest in recorded().items()
               if name in files and hashlib.sha256(files[name].read_bytes()).hexdigest() != digest]
    assert not changed, 'Edited in the copy (fix them in App Base, then update-base.ps1): {}'.format(changed)


def test_the_copy_has_every_file_and_no_more():
    assert set(present()) == set(recorded())

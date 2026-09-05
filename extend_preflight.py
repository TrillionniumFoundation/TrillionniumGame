"""Extend the fixed I/O preflight with the reproduced exact dependency mismatch."""
from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parent
source = (ROOT / 'stage.py').read_bytes()
if hashlib.sha256(source).hexdigest() != 'd38e032e77f06df4ab91cd670554085ef2d0f396668eed6637515b4168f96408':
    raise SystemExit('original preflight bytes changed')
test_path = ROOT / 'dependency-test.py'
test_source = test_path.read_bytes()
if hashlib.sha256(test_source).hexdigest() != '062d8b5f35a803093fea488988ef160fd4cfd744892f9ad9c27f899dfaccc83b':
    raise SystemExit('dependency regression bytes changed')
text = source.decode()
replacements = [
    ('PATHS = EXISTING | NEW',
     "EXISTING.add('scripts/check-rust-foundation.py')\nNEW.add('tests/control_plane/test_rust_foundation_dependency_alignment.py')\nPATHS = EXISTING | NEW"),
    ("set(payload['files']) == NEW", "set(payload['files']) == NEW - {'tests/control_plane/test_rust_foundation_dependency_alignment.py'}"),
    ("    reproduce()\n    delta", "    require(blob(original['scripts/check-rust-foundation.py']) == 'e22677942ef8e67fa19096407f072249c7a4a3c9', 'dependency checker baseline changed')\n    reproduce()\n    delta"),
    ("    changed = dict(payload['files'])", """    changed = dict(payload['files'])
    checker = original['scripts/check-rust-foundation.py'].decode()
    anchor = '        "native-tls": "=0.2.18",\\n'
    require(checker.count(anchor) == 1, 'dependency anchor not unique')
    changed['scripts/check-rust-foundation.py'] = checker.replace(anchor, anchor + '        "openssl": "=0.10.81",\\n', 1)
    changed['tests/control_plane/test_rust_foundation_dependency_alignment.py'] = (Path(__file__).resolve().parent / 'dependency-test.py').read_text(encoding='utf-8')"""),
    ("doc.rstrip() + payload['doc_append']", "doc.rstrip() + payload['doc_append'] + '\\nThe foundation dependency checker also binds the existing diagnostic OpenSSL dependency\\nexactly to `=0.10.81`, matching Cargo.toml and the already pinned Cargo.lock.\\nIt does not allow version ranges, alternate sources, extra dependencies or pure-core\\nOpenSSL imports. `test_rust_foundation_dependency_alignment.py` checks positive\\nrepository validation and rejected mutations, including other pins and runtime\\nfeature changes. This alignment is not an independent cryptographic approval.\\n'"),
    ("['check-documentation-authority', 'check-plan', 'check-evidence-index',", "['check-rust-foundation', 'check-documentation-authority', 'check-plan', 'check-evidence-index',"),
    ("'full_python_tests': 370, 'new_io_tests': 34,", "'full_python_tests': 380, 'new_dependency_tests': 10, 'new_io_tests': 34,"),
    ("int(count.group(1)) == 370", "int(count.group(1)) == 380"),
    ("'consumer integration tests not collected')", "'consumer integration tests not collected')\n            require(output.count('(control_plane.test_rust_foundation_dependency_alignment.') == 10, 'dependency alignment tests not collected')"),
    ("len(receipt['entries']) == 7", "len(receipt['entries']) == 9"),
    ("len(receipt['checks']) == 9", "len(receipt['checks']) == 10"),
]
for before, after in replacements:
    if text.count(before) != 1:
        raise SystemExit('preflight extension anchor changed')
    text = text.replace(before, after, 1)
if sys.argv[1:] == ['--compile-only']:
    compile(text, str(ROOT / 'stage-expanded.py'), 'exec')
    (ROOT / 'stage-expanded.py').write_text(text)
    print('expanded preflight compilation passed')
else:
    exec(compile(text, str(ROOT / 'stage-expanded.py'), 'exec'),
         {'__name__': '__main__', '__file__': str(ROOT / 'stage-expanded.py')})

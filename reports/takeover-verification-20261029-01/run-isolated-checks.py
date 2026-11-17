from pathlib import Path
from unittest.mock import patch
from scripts import demo
from ekb import evaluate
root = Path.cwd()
output = root / 'reports/takeover-verification-20261029-01'
original = Path.write_text
redirects = {
    root / 'reports/http-demo.json': output / 'http-demo.json',
    root / 'reports/latest-evaluation.txt': output / 'latest-evaluation.txt',
}
def preserve_history(path, data, *args, **kwargs):
    return original(redirects.get(path.resolve(), path), data, *args, **kwargs)
with patch.object(Path, 'write_text', preserve_history):
    evaluate.run(mode='bm25', config_path=None)
    demo.run()

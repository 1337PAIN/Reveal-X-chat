"""/lab can run every attack on one image and show the README's table live.

The README states, for the seven attacks, whether the ML and the HMAC each
detect it -- with random noise the one the ML misses. That was a claim to
take on trust or check in the test suite; "Run all attacks" now reproduces
it on whatever image is uploaded. Requests go one at a time, and a
rate-limit answer is waited out rather than failing the run halfway
(the lab allows 20 a minute; seven fit). The row logic is tested in
tests/js/lab-format.test.mjs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')
FORMAT = (ROOT / 'app' / 'static' / 'js' / 'lab-format.js').read_text(encoding='utf-8')
README = (ROOT / 'README.md').read_text(encoding='utf-8')


def readme_attacks():
    table = README[README.index('| Attack | ML detects | HMAC detects |'):]
    rows = table.split('\n\n')[0].splitlines()[2:]
    return [row.split('|')[1].strip().strip('*') for row in rows]


def test_the_page_runs_the_attacks_the_readme_lists():
    assert re.findall(r"label: '([^']+)'", FORMAT) == readme_attacks()


def test_there_is_a_button_and_a_table_to_fill():
    assert re.search(r'<button id="runAllBtn"[^>]*type="button"[^>]*disabled[^>]*>Run all attacks</button>', LAB)
    table = LAB[LAB.index('id="attackTable"'):]
    table = table[:table.index('</table>')]
    assert '<caption' in table
    for header in ('Attack', 'ML detects', 'HMAC detects', 'PSNR', 'SSIM'):
        assert f'<th scope="col">{header}</th>' in table
    assert 'id="attackTableBody"' in table


def test_progress_is_announced():
    tag = re.search(r'<p id="statusText"[^>]*>', LAB).group(0)
    assert 'role="status"' in tag


def test_the_run_is_sequential_and_waits_out_the_limit():
    run = LAB_JS[LAB_JS.index('async function runAllAttacks('):]
    run = run[:run.index('\n}\n')]
    assert 'for (const' in run and 'await processOnce(' in run
    assert 'attackRow(' in run
    once = LAB_JS[LAB_JS.index('async function processOnce('):]
    once = once[:once.index('\n}\n')]
    assert 'retryAfter(' in once and 'res.status === 429' in once
    assert 'innerHTML' not in run

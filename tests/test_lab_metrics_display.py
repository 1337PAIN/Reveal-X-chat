"""/lab shows every metric through lab-format.js's metric().

The rounding itself is tested in tests/js/lab-format.test.mjs. This checks
the page uses it: no metric is written straight from the response, where it
arrived with four decimals.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')


def test_no_metric_is_printed_raw():
    # A template or assignment that interpolates data.metrics... directly.
    raw = re.findall(r'\$\{data\.metrics\.[a-z_]+\.(?:mse|psnr|ssim)\}', LAB_JS)
    assert raw == []
    assert not re.search(r'textContent = data\.metrics\.[a-z_]+\.(?:mse|psnr|ssim)', LAB_JS)


def test_the_dashboard_goes_through_the_formatter():
    assert LAB_JS.count('metric(') >= 9


def test_the_lab_page_never_uses_innerHTML():
    # The one use cleared the explanation list (innerHTML = ''); harmless,
    # but the house rule is none at all, so a future edit cannot slide data
    # into it. replaceChildren() does the clearing.
    assert 'innerHTML' not in LAB_JS

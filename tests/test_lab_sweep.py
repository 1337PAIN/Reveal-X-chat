"""/lab sweeps one attack across strengths and charts it, with a table too.

One run at one strength says little about how an attack degrades the image
or when the ML starts to notice. "Sweep strength" runs the chosen attack at
six strengths and draws reconstruction SSIM and the ML's tamper probability
as an inline SVG -- built with createElementNS, no chart library, nothing
third-party. The same numbers are in a table under the chart, and the chart
has a one-line text alternative. Geometry is tested in
tests/js/lab-format.test.mjs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'css' / 'rx_lab.css').read_text(encoding='utf-8')


def test_there_is_a_sweep_button():
    assert re.search(r'<button id="sweepBtn"[^>]*type="button"[^>]*disabled[^>]*>Sweep strength</button>', LAB)


def test_the_chart_is_an_image_with_a_text_alternative():
    svg = re.search(r'<svg id="sweepChart"[^>]*>', LAB).group(0)
    assert 'role="img"' in svg and 'aria-labelledby="sweepSummary"' in svg
    assert 'id="sweepSummary"' in LAB


def test_the_series_differ_by_more_than_colour():
    # A dashed line and different markers, and a legend in words.
    assert re.search(r'\.sweep-ml\s*\{[^}]*stroke-dasharray', CSS)
    legend = LAB[LAB.index('class="sweep-legend"'):]
    legend = legend[:legend.index('</ul>')]
    assert 'Reconstruction SSIM' in legend and 'ML tamper probability' in legend


def test_the_numbers_are_in_a_table_as_well():
    table = LAB[LAB.index('id="sweepTable"'):]
    table = table[:table.index('</table>')]
    for header in ('Strength', 'SSIM', 'ML tamper probability', 'ML verdict'):
        assert f'<th scope="col">{header}</th>' in table


def test_it_is_drawn_without_innerHTML_and_runs_one_at_a_time():
    draw = LAB_JS[LAB_JS.index('function drawSweep('):]
    draw = draw[:draw.index('\n}\n')]
    assert 'svgEl(' in draw and 'sweepChart(' in draw
    helper = LAB_JS[LAB_JS.index('function svgEl('):]
    helper = helper[:helper.index('\n}\n')]
    assert 'createElementNS' in helper and 'innerHTML' not in helper
    assert 'innerHTML' not in draw
    run = LAB_JS[LAB_JS.index('async function runSweep('):]
    run = run[:run.index('\n}\n')]
    assert 'SWEEP_STRENGTHS' in run and 'await processOnce(' in run


def test_labels_keep_their_size_on_a_phone():
    # A fixed viewBox scaled down to 375px shrank the 12px labels to ~6px.
    draw = LAB_JS[LAB_JS.index('function drawSweep('):]
    draw = draw[:draw.index('\n}\n')]
    assert 'getBoundingClientRect().width' in draw
    assert "setAttribute('viewBox'" in draw

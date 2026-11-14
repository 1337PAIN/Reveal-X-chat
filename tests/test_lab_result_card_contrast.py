"""/lab's result cards keep their dark scrim in every state.

The cards are translucent over a fixed aurora. Their "danger" and "success"
states replaced the card's dark panel with a light tint instead of laying
the tint over it, so the aurora showed straight through: with every /lab
section filled in, the contrast audit measured "Cryptographic integrity" at
4.19:1 (below AA) and "Tampered" at 3.86:1. The 56-element audit had never
seen a result state. And the resting cards, at --panel's 0.55 alpha, fell to
6.08-6.51:1 over the brightest part of the aurora.

Result cards now sit on --panel-3, and the state tint is a gradient layered
over it. Measured with the page filled in: labels 7.99-8.39:1, the large
state words 6.37-6.82:1 (AAA for large text is 4.5).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'rx_lab.css').read_text(encoding='utf-8')


def rule(selector):
    return re.search(r'\n' + re.escape(selector) + r'\s*\{([^}]*)\}', CSS).group(1)


def test_result_cards_sit_on_the_densest_panel():
    assert re.search(r'background:\s*var\(--panel-3\)', rule('.summary-card'))


def test_state_tints_are_layered_over_the_panel_not_instead_of_it():
    for state in ('.summary-card.danger', '.summary-card.success'):
        background = re.search(r'background:\s*([^;]*);', rule(state)).group(1)
        assert background.startswith('linear-gradient('), state
        assert background.rstrip().endswith('var(--panel-3)'), state

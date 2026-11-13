"""The chat client's source, for the tests that check how it is wired.

chat.js was one file and these tests read it whole. It is now ES modules in
app/static/js/chat/; this returns them joined in the order main.js loads them,
which is the order the sections of chat.js ran.
"""

import re
from pathlib import Path

CHAT_DIR = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js' / 'chat'


def module_order():
    main = (CHAT_DIR / 'main.js').read_text(encoding='utf-8')
    return re.findall(r"import '\./(\w+)\.js';", main)


def chat_source():
    return '\n'.join((CHAT_DIR / f'{name}.js').read_text(encoding='utf-8')
                     for name in module_order())

"""routes.py is split into app/handlers/; nothing may have been lost in the move.

The split moved code verbatim between modules, which is exactly how a helper
ends up defined in one module and called from another that never imports it:
a NameError that only fires when that handler runs. One did, during the
split itself (_share_hmac, used by the share upload). This checks every
handler module for it statically, for every function, without running any.
"""

import builtins
import dis
import importlib
import inspect
import types
from pathlib import Path

import pytest

from app import app as flask_app, socketio

HANDLERS = Path(__file__).resolve().parent.parent / 'app' / 'handlers'
MODULES = sorted(p.stem for p in HANDLERS.glob('*.py') if p.stem != '__init__')


def global_loads(code):
    names = {ins.argval for ins in dis.get_instructions(code) if ins.opname == 'LOAD_GLOBAL'}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= global_loads(const)
    return names


@pytest.mark.parametrize('name', MODULES)
def test_every_global_a_handler_uses_exists_in_its_module(name):
    module = importlib.import_module(f'app.handlers.{name}')
    missing = {}
    for attr, obj in vars(module).items():
        func = inspect.unwrap(obj) if callable(obj) else None
        if not isinstance(func, types.FunctionType) or func.__module__ != module.__name__:
            continue
        unresolved = {n for n in global_loads(func.__code__)
                      if n not in vars(module) and not hasattr(builtins, n)}
        if unresolved:
            missing[attr] = sorted(unresolved)
    assert not missing, f'{name}: {missing}'


def test_routes_is_only_a_compatibility_module():
    source = (HANDLERS.parent / 'routes.py').read_text(encoding='utf-8')
    assert 'def ' not in source and '@socketio' not in source
    assert len(source.splitlines()) < 40


def test_every_route_and_event_is_still_registered():
    events = set(socketio.server.handlers['/'])
    assert {'login', 'send_message', 'send_share', 'group_send', 'resume_session',
            'admin_set_state', 'delete_account', 'connect', 'disconnect'} <= events
    rules = {rule.rule for rule in flask_app.url_map.iter_rules()}
    assert {'/', '/lab', '/share1/<message_id>', '/api/share/<message_id>/analysis'} <= rules

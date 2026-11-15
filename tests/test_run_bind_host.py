"""run.py can be told to listen on this machine only.

Behind a tunnel the app trusts forwarded headers (REVEALX_TRUST_PROXY), so the
only thing that may reach it is the tunnel. run.py listened on 0.0.0.0, which
let anyone on the same network connect directly and send a forged
X-Forwarded-For or CF-Connecting-IP. REVEALX_BIND_HOST=127.0.0.1 closes that;
unset, it keeps listening on every interface, as development expects.
"""

import importlib.util
from pathlib import Path

import pytest

RUN = Path(__file__).resolve().parent.parent / 'run.py'


def _load():
    spec = importlib.util.spec_from_file_location('run_script', RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_is_every_interface(monkeypatch):
    monkeypatch.delenv('REVEALX_BIND_HOST', raising=False)
    assert _load().bind_host() == '0.0.0.0'


@pytest.mark.parametrize('value', ['127.0.0.1', '::1', '0.0.0.0'])
def test_the_variable_chooses_the_address(monkeypatch, value):
    monkeypatch.setenv('REVEALX_BIND_HOST', value)
    assert _load().bind_host() == value


def test_the_port_probe_and_the_server_use_the_same_address():
    source = RUN.read_text(encoding='utf-8')
    assert "probe.bind((host, port))" in source
    assert "host=host" in source

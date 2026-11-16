"""Helpers shared by the browser end-to-end tests.

In a module of its own, not conftest.py: tests/ already has a conftest.py,
and two modules both imported as "conftest" collide in a full run.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVE = Path(__file__).resolve().parent / 'serve.py'
# Uploaded by the CI job as an artifact; ignored by git.
ARTIFACTS = Path(__file__).resolve().parent / 'artifacts'

ALICE = ('e2ealice', 'e2e-local-pass-a1')
BOB = ('e2ebob', 'e2e-local-pass-b2')
CAROL = ('e2ecarol', 'e2e-local-pass-c3')


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Server:
    """One app process. restart() keeps the port and the database, which is
    what a dropped connection looks like from the browser."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.port = _free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.log = open(data_dir / 'server.log', 'a', encoding='utf-8')
        self.proc = None

    def start(self):
        env = dict(os.environ, REVEALX_SWEEPER='off', PYTHONUNBUFFERED='1')
        self.proc = subprocess.Popen([sys.executable, str(SERVE), str(self.data_dir), str(self.port)],
                                     cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f'server exited; see {self.data_dir / "server.log"}')
            try:
                urllib.request.urlopen(self.url + '/api/auth/config', timeout=1)
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError('server did not answer within 60 s')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def restart(self):
        self.stop()
        self.start()


def new_page(browser, width=1440, height=900):
    # Service workers are blocked so every request reaches the server under
    # test: a cached shell from an earlier run must not be what passes.
    context = browser.new_context(service_workers='block',
                                  viewport={'width': width, 'height': height})
    context.set_default_timeout(15000)
    page = context.new_page()
    page.errors = []
    page.on('pageerror', lambda error: page.errors.append(str(error)))
    return page


def sign_in(page, server, account):
    username, password = account
    page.goto(server.url + '/')
    page.fill('#usernameInput', username)
    page.fill('#passwordInput', password)
    page.click('#loginBtn')
    page.wait_for_function(
        "name => document.getElementById('accountName').textContent.trim() === name", arg=username)
    # The key pair is generated and published after sign-in.
    page.wait_for_function('() => window.revealxE2EE && window.revealxE2EE.isReady()')
    return page


def shot(page, name):
    ARTIFACTS.mkdir(exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'{name}.png'), full_page=True)

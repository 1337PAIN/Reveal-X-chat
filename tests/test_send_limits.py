"""Per-user token buckets on the send paths.

Sign-in, registration and the lab endpoints were limited; sending was not.
One signed-in socket could emit send_message in a loop and fill the database
and every recipient's screen, and send_image is worse: each one decodes an
image, splits it, writes a file and runs the tamper detector, on a server
that runs one worker.

Each user has a bucket per kind of send. It holds a burst and refills
steadily, so ordinary use never notices it and a loop runs dry quickly.
"""

import base64
import uuid

import cv2
import numpy as np
import pytest

from app import app as flask_app, socketio
from app import routes

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(routes, '_send_clock', c)
    return c


@pytest.fixture
def pair(store):
    _received.clear()
    alice, _ = store.create_user('alice', 'correct-horse-1')
    bob, _ = store.create_user('bob', 'correct-horse-2')
    socks = []
    for name, pw in (('alice', 'correct-horse-1'), ('bob', 'correct-horse-2')):
        s = socketio.test_client(flask_app)
        s.emit('login', {'username': name, 'password': pw})
        socks.append(s)
    yield socks[0], socks[1], alice, bob
    for s in socks:
        s.disconnect()


def too_fast(sock, channel='message_error'):
    return [e for e in events(sock, channel)
            if 'too fast' in e['args'][0].get('error', '').lower()]


def test_text_has_a_burst_and_then_stops(pair, store, clock):
    a, b, alice, bob = pair
    burst = routes.SEND_LIMITS['message'][0]
    for i in range(burst + 5):
        a.emit('send_message', {'recipient_id': bob['id'], 'message': f'm{i}'})
    assert len(store.get_conversation(alice['id'], bob['id'])) == burst
    assert too_fast(a)


def test_the_bucket_refills_over_time(pair, store, clock):
    a, b, alice, bob = pair
    burst, rate = routes.SEND_LIMITS['message']
    for i in range(burst + 1):
        a.emit('send_message', {'recipient_id': bob['id'], 'message': f'm{i}'})
    clock.now += 2 / rate
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'later'})
    assert store.get_conversation(alice['id'], bob['id'])[-1]['content'] == 'later'


def test_buckets_are_per_user(pair, store, clock):
    a, b, alice, bob = pair
    burst = routes.SEND_LIMITS['message'][0]
    for i in range(burst + 3):
        a.emit('send_message', {'recipient_id': bob['id'], 'message': f'm{i}'})
    b.emit('send_message', {'recipient_id': alice['id'], 'message': 'unaffected'})
    assert not too_fast(b)


def test_voice_has_its_own_smaller_bucket(pair, store, clock):
    a, b, alice, bob = pair
    burst = routes.SEND_LIMITS['voice'][0]
    assert burst < routes.SEND_LIMITS['message'][0]
    for _ in range(burst + 2):
        a.emit('send_voice', {'recipient_id': bob['id'],
                              'audio': base64.b64encode(b'\x01' * 64).decode(),
                              'iv': 'aXY=', 'mime': 'audio/webm', 'encrypted': True,
                              'v': 2, 'msg_uuid': str(uuid.uuid4())})
    voices = [m for m in store.get_conversation(alice['id'], bob['id']) if m['type'] == 'voice']
    assert len(voices) == burst
    assert too_fast(a)
    # Running out of voice does not stop text.
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'text still works'})
    assert store.get_conversation(alice['id'], bob['id'])[-1]['content'] == 'text still works'


def test_images_are_refused_before_any_work_is_done(pair, store, clock, monkeypatch):
    from share_helpers import share_payload
    a, b, alice, bob = pair
    burst = routes.SEND_LIMITS['image'][0]
    decoded = []
    real = routes._validate_share1_png
    monkeypatch.setattr(routes, '_validate_share1_png',
                        lambda value: decoded.append(1) or real(value))
    for i in range(burst + 3):
        a.emit('send_share', share_payload(bob['id'], seed=i))
    assert len(decoded) == burst, 'shares past the limit were still decoded'
    assert too_fast(a, 'image_error')


def test_group_sends_draw_on_the_text_bucket(pair, store, clock):
    a, b, alice, bob = pair
    group, _ = store.create_group(alice['id'], 'team', '', [bob['id']])
    burst = routes.SEND_LIMITS['message'][0]
    for i in range(burst + 2):
        a.emit('group_send', {'group_id': group['id'], 'payloads': {
            alice['id']: {'content': f'g{i}', 'iv': ''}, bob['id']: {'content': f'g{i}', 'iv': ''}}})
    errors = [e for e in events(a, 'group_error') if 'too fast' in e['args'][0]['error'].lower()]
    assert errors

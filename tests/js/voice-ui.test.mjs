/**
 * The voice note player and recorder meter (app/static/js/voice-ui.js).
 *
 * Voice notes used the browser's own <audio controls>: a grey bar that looks
 * different in every browser, shows "0:00 / Infinity" for a fresh WebM
 * recording (MediaRecorder writes no duration), and cannot be themed. These
 * test the rules of its replacement -- the waveform, the clock, seeking from
 * the keyboard, the speed cycle, one note playing at a time, and that it is
 * built as elements with names rather than markup -- not how it looks.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeDocument } from './harness.mjs';

const window = {};
loadBrowserScript(join(STATIC_JS, 'voice-ui.js'), { window });
const ui = window.revealxVoiceUI;

/** A stand-in <audio>: records what the player asks of it. */
function fakeAudio(duration = 8) {
    const listeners = {};
    return {
        paused: true,
        currentTime: 0,
        duration,
        playbackRate: 1,
        plays: 0,
        play() { this.paused = false; this.plays += 1; (listeners.play || []).forEach((f) => f()); return Promise.resolve(); },
        pause() { this.paused = true; (listeners.pause || []).forEach((f) => f()); },
        addEventListener(kind, fn) { (listeners[kind] ||= []).push(fn); },
        fire(kind) { (listeners[kind] || []).forEach((f) => f()); },
    };
}

function key(name) {
    return { key: name, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
}

// ------------------------------------------------------------------- clock

test('the clock reads m:ss and never shows Infinity or NaN', () => {
    assert.equal(ui.formatClock(0), '0:00');
    assert.equal(ui.formatClock(7.9), '0:07');
    assert.equal(ui.formatClock(65), '1:05');
    for (const bad of [Infinity, NaN, -3, undefined]) assert.equal(ui.formatClock(bad), '0:00');
});

// ---------------------------------------------------------------- waveform

test('peaks are one value per bar, in 0..1, with the loudest bar at 1', () => {
    const samples = new Float32Array(1000);
    for (let i = 0; i < samples.length; i += 1) samples[i] = Math.sin(i / 7) * (i / samples.length);
    const peaks = ui.peaksFromSamples(samples, 32);
    assert.equal(peaks.length, 32);
    assert.ok(peaks.every((p) => p >= 0 && p <= 1));
    assert.equal(Math.max(...peaks), 1);
    assert.ok(peaks[31] > peaks[1], 'louder at the end, as the signal is');
});

test('silence still draws a visible line, not nothing', () => {
    const peaks = ui.peaksFromSamples(new Float32Array(500), 20);
    assert.equal(peaks.length, 20);
    assert.ok(peaks.every((p) => p > 0));
});

test('fewer samples than bars still gives every bar a value', () => {
    const peaks = ui.peaksFromSamples(new Float32Array([0.5, -0.2, 0.9]), 10);
    assert.equal(peaks.length, 10);
    assert.ok(peaks.every((p) => Number.isFinite(p)));
});

test('a placeholder waveform is stable for the same message', () => {
    assert.deepEqual(ui.placeholderPeaks('msg-42', 24), ui.placeholderPeaks('msg-42', 24));
    assert.notDeepEqual(ui.placeholderPeaks('msg-42', 24), ui.placeholderPeaks('msg-43', 24));
});

test('heights are drawn by class, from a fixed set, never by inline style', () => {
    assert.equal(ui.levelClass(0), 'lv1');
    assert.equal(ui.levelClass(1), 'lv8');
    assert.equal(ui.levelClass(0.5), 'lv4');
});

// ------------------------------------------------------------- the player

test('the player is built from elements with names, and no markup', () => {
    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 30 });
    assert.equal(doc.innerHTMLWrites, 0);
    assert.equal(parts.play.tagName, 'BUTTON');
    assert.equal(parts.play.getAttribute('aria-label'), 'Play voice message');
    assert.equal(parts.wave.getAttribute('role'), 'slider');
    assert.equal(parts.wave.getAttribute('tabindex'), '0');
    assert.equal(parts.wave.getAttribute('aria-label'), 'Position in voice message');
    assert.equal(parts.bars.length, 30);
    assert.equal(parts.speed.tagName, 'BUTTON');
    assert.equal(parts.speed.textContent, '1×');
});

test('play and pause drive the audio and the button says which comes next', () => {
    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 10 });
    const audio = fakeAudio();
    ui.attachVoicePlayer(parts, audio, { duration: 8 });
    parts.play.click();
    assert.equal(audio.paused, false);
    assert.equal(parts.play.getAttribute('aria-label'), 'Pause voice message');
    parts.play.click();
    assert.equal(audio.paused, true);
    assert.equal(parts.play.getAttribute('aria-label'), 'Play voice message');
});

test('progress fills the bars already played and reports the time', () => {
    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 10 });
    const audio = fakeAudio();
    ui.attachVoicePlayer(parts, audio, { duration: 8 });
    audio.currentTime = 4;
    audio.fire('timeupdate');
    const played = parts.bars.filter((b) => b.classList.contains('played')).length;
    assert.equal(played, 5);
    assert.equal(parts.time.textContent, '0:04');
    assert.equal(parts.wave.getAttribute('aria-valuenow'), '4');
    assert.equal(parts.wave.getAttribute('aria-valuetext'), '0:04 of 0:08');
});

test('a WebM recording with no duration uses the decoded one', () => {
    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 10 });
    const audio = fakeAudio(Infinity);
    ui.attachVoicePlayer(parts, audio, { duration: 0 });
    ui.setDuration(parts, 6.2);
    assert.equal(parts.time.textContent, '0:06');
    assert.equal(parts.wave.getAttribute('aria-valuemax'), '6');
});

test('arrow keys seek by a second, Home and End jump, other keys are left alone', () => {
    assert.equal(ui.seekFromKey('ArrowRight', 3, 8), 4);
    assert.equal(ui.seekFromKey('ArrowLeft', 0.5, 8), 0);
    assert.equal(ui.seekFromKey('ArrowUp', 7.5, 8), 8);
    assert.equal(ui.seekFromKey('Home', 5, 8), 0);
    assert.equal(ui.seekFromKey('End', 5, 8), 8);
    assert.equal(ui.seekFromKey('PageUp', 1, 8), 6);
    assert.equal(ui.seekFromKey('a', 5, 8), null);

    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 10 });
    const audio = fakeAudio();
    ui.attachVoicePlayer(parts, audio, { duration: 8 });
    const event = key('ArrowRight');
    (parts.wave.listeners.keydown || []).forEach((f) => f(event));
    assert.equal(audio.currentTime, 1);
    assert.equal(event.defaultPrevented, true);
});

test('speed cycles 1x, 1.5x, 2x and back, and the button says it', () => {
    assert.equal(ui.nextSpeed(1), 1.5);
    assert.equal(ui.nextSpeed(1.5), 2);
    assert.equal(ui.nextSpeed(2), 1);
    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 10 });
    const audio = fakeAudio();
    ui.attachVoicePlayer(parts, audio, { duration: 8 });
    parts.speed.click();
    assert.equal(audio.playbackRate, 1.5);
    assert.equal(parts.speed.textContent, '1.5×');
    assert.equal(parts.speed.getAttribute('aria-label'), 'Playback speed 1.5 times, change');
});

test('starting one voice note pauses any other', () => {
    const doc = makeDocument();
    const a = ui.buildVoicePlayer(doc, { bars: 5 });
    const b = ui.buildVoicePlayer(doc, { bars: 5 });
    const audioA = fakeAudio();
    const audioB = fakeAudio();
    ui.attachVoicePlayer(a, audioA, { duration: 8 });
    ui.attachVoicePlayer(b, audioB, { duration: 8 });
    a.play.click();
    b.play.click();
    assert.equal(audioA.paused, true);
    assert.equal(audioB.paused, false);
});

test('reaching the end resets to the start, ready to play again', () => {
    const doc = makeDocument();
    const parts = ui.buildVoicePlayer(doc, { bars: 10 });
    const audio = fakeAudio();
    ui.attachVoicePlayer(parts, audio, { duration: 8 });
    parts.play.click();
    audio.currentTime = 8;
    audio.paused = true;
    audio.fire('ended');
    assert.equal(audio.currentTime, 0);
    assert.equal(parts.play.getAttribute('aria-label'), 'Play voice message');
    assert.equal(parts.bars.filter((b) => b.classList.contains('played')).length, 0);
});

// ---------------------------------------------------------- recorder meter

test('the live meter shows a microphone level from the analyser bytes', () => {
    const quiet = new Uint8Array(256).fill(128);
    assert.equal(ui.levelFromTimeDomain(quiet), 0);
    const loud = Uint8Array.from({ length: 256 }, (_, i) => (i % 2 ? 255 : 0));
    assert.ok(ui.levelFromTimeDomain(loud) > 0.9);
});

test('the meter scrolls: the newest level is on the right', () => {
    const doc = makeDocument();
    const meter = ui.buildLevelMeter(doc, 6);
    assert.equal(meter.root.getAttribute('aria-hidden'), 'true');
    assert.equal(meter.bars.length, 6);
    meter.push(1);
    assert.ok(meter.bars[5].classList.contains('lv8'));
    meter.push(0);
    assert.ok(meter.bars[4].classList.contains('lv8'));
    assert.ok(meter.bars[5].classList.contains('lv1'));
    meter.reset();
    assert.ok(meter.bars.every((b) => b.classList.contains('lv1')));
});

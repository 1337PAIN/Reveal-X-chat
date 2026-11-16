/**
 * Reveal-X: the voice note player and the recorder's level meter.
 *
 * Voice notes used the browser's own <audio controls>: a grey bar that looks
 * different in every browser, cannot be themed, and shows "0:00 / Infinity"
 * for a fresh recording, because MediaRecorder writes WebM with no duration.
 * This replaces it with what a chat app is expected to show -- a play button,
 * a waveform drawn from the recording itself that fills in as it plays, the
 * time, and a speed button -- while the <audio> element stays underneath as
 * the engine, hidden.
 *
 * Accessibility is part of the design rather than added after:
 *   - the play and speed controls are real buttons, named for what they do
 *     next ("Pause voice message", "Playback speed 1.5 times, change");
 *   - the waveform is a slider: focusable, with its position spoken as
 *     "0:04 of 0:08", and Arrow keys, Page keys, Home and End to seek;
 *   - heights and progress are classes, never inline styles, so the CSP
 *     needs nothing new and both themes style them in the stylesheet.
 *
 * Starting one note pauses any other, as people expect.
 *
 * A classic script, like send-policy.js, so tests/js/voice-ui.test.mjs loads
 * the shipped file through harness.mjs. The live audio decoding and the
 * microphone analyser are browser APIs and live in chat/render.js and
 * chat/messaging.js; the arithmetic they feed is here, where it is tested.
 */

(function () {
    const LEVELS = 8;            // bar heights: classes lv1 (lowest) .. lv8
    const SPEEDS = [1, 1.5, 2];
    const STEP_SECONDS = 1;      // Arrow keys
    const PAGE_SECONDS = 5;      // Page Up / Page Down

    /** "m:ss", and 0:00 for anything that is not a usable number of seconds. */
    function formatClock(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
        const whole = Math.floor(seconds);
        return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
    }

    /**
     * One loudness value per bar, 0..1, the loudest bar at 1. RMS of each
     * slice of the samples; a floor keeps silence visible as a line.
     */
    function peaksFromSamples(samples, bars) {
        const out = new Array(bars).fill(0);
        const length = samples ? samples.length : 0;
        if (length) {
            for (let bar = 0; bar < bars; bar += 1) {
                const start = Math.floor((bar * length) / bars);
                const end = Math.max(start + 1, Math.floor(((bar + 1) * length) / bars));
                let sum = 0;
                let count = 0;
                for (let i = start; i < end && i < length; i += 1) {
                    sum += samples[i] * samples[i];
                    count += 1;
                }
                out[bar] = count ? Math.sqrt(sum / count) : 0;
            }
        }
        const max = Math.max(...out);
        return out.map((value) => (max > 0 ? Math.max(0.08, value / max) : 0.08));
    }

    /**
     * A waveform for a note whose audio this browser cannot decode (an
     * engine that cannot read the container). Varied rather than flat, so it
     * still reads as a voice note, and stable for the same message.
     */
    function placeholderPeaks(seed, bars) {
        let hash = 2166136261;
        const text = String(seed);
        for (let i = 0; i < text.length; i += 1) {
            hash ^= text.charCodeAt(i);
            hash = Math.imul(hash, 16777619) >>> 0;
        }
        const out = [];
        for (let bar = 0; bar < bars; bar += 1) {
            hash = Math.imul(hash ^ (hash >>> 15), 2246822507) >>> 0;
            hash = Math.imul(hash ^ (hash >>> 13), 3266489909) >>> 0;
            out.push(0.25 + ((hash % 1000) / 1000) * 0.75);
        }
        return out;
    }

    /** The class for a height in 0..1: eighths, lv1 (silence) to lv8 (full). */
    function levelClass(value) {
        const clamped = Math.min(1, Math.max(0, Number(value) || 0));
        return `lv${Math.min(LEVELS, Math.max(1, Math.ceil(clamped * LEVELS)))}`;
    }

    function setLevel(bar, value) {
        for (let level = 1; level <= LEVELS; level += 1) bar.classList.remove(`lv${level}`);
        bar.classList.add(levelClass(value));
    }

    /** Where a key moves the playhead, or null for a key that is not a seek. */
    function seekFromKey(key, current, duration) {
        const clamp = (value) => Math.min(duration, Math.max(0, value));
        switch (key) {
            case 'ArrowRight':
            case 'ArrowUp': return clamp(current + STEP_SECONDS);
            case 'ArrowLeft':
            case 'ArrowDown': return clamp(current - STEP_SECONDS);
            case 'PageUp': return clamp(current + PAGE_SECONDS);
            case 'PageDown': return clamp(current - PAGE_SECONDS);
            case 'Home': return 0;
            case 'End': return duration;
            default: return null;
        }
    }

    function nextSpeed(rate) {
        const index = SPEEDS.indexOf(rate);
        return SPEEDS[(index + 1) % SPEEDS.length];
    }

    function speedLabel(rate) {
        return `${rate}×`;
    }

    function icon(doc, name) {
        if (window.revealxIcons) return window.revealxIcons.icon(doc, name);
        // Without the sprite helper (it loads first in the app) a blank,
        // hidden element keeps the button's shape; the name still speaks.
        const span = doc.createElement('span');
        span.setAttribute('aria-hidden', 'true');
        return span;
    }

    /**
     * The player's elements. Nothing is wired yet; attachVoicePlayer does
     * that once the audio exists.
     * @returns {{root, play, wave, bars, time, speed}}
     */
    function buildVoicePlayer(doc, options = {}) {
        const barCount = options.bars || 36;
        const root = doc.createElement('div');
        root.className = 'voice-note';

        const play = doc.createElement('button');
        play.setAttribute('type', 'button');
        play.className = 'voice-play';
        play.setAttribute('aria-label', 'Play voice message');
        play.append(icon(doc, 'play'));

        const wave = doc.createElement('div');
        wave.className = 'voice-wave';
        wave.setAttribute('role', 'slider');
        wave.setAttribute('tabindex', '0');
        wave.setAttribute('aria-label', 'Position in voice message');
        wave.setAttribute('aria-valuemin', '0');
        wave.setAttribute('aria-valuemax', '0');
        wave.setAttribute('aria-valuenow', '0');
        wave.setAttribute('aria-valuetext', '0:00 of 0:00');
        const bars = [];
        for (let i = 0; i < barCount; i += 1) {
            const bar = doc.createElement('span');
            bar.className = 'voice-bar';
            setLevel(bar, 0.08);
            wave.append(bar);
            bars.push(bar);
        }

        const time = doc.createElement('span');
        time.className = 'voice-time';
        time.textContent = '0:00';

        const speed = doc.createElement('button');
        speed.setAttribute('type', 'button');
        speed.className = 'voice-speed';
        speed.textContent = speedLabel(1);
        speed.setAttribute('aria-label', 'Playback speed 1 times, change');

        root.append(play, wave, time, speed);
        return { root, play, wave, bars, time, speed, duration: 0 };
    }

    /** Draw a waveform (one 0..1 value per bar) onto a built player. */
    function setPeaks(parts, peaks) {
        parts.bars.forEach((bar, i) => setLevel(bar, peaks[i] === undefined ? 0.08 : peaks[i]));
    }

    function updatePosition(parts, current) {
        const duration = parts.duration;
        const fraction = duration > 0 ? Math.min(1, Math.max(0, current / duration)) : 0;
        const played = Math.round(fraction * parts.bars.length);
        parts.bars.forEach((bar, i) => bar.classList.toggle('played', i < played));
        const shown = current > 0 ? current : duration;
        parts.time.textContent = formatClock(shown);
        parts.wave.setAttribute('aria-valuenow', String(Math.floor(current)));
        parts.wave.setAttribute('aria-valuetext', `${formatClock(current)} of ${formatClock(duration)}`);
    }

    /** The length in seconds, from the decoded audio when the file has none. */
    function setDuration(parts, seconds) {
        if (!Number.isFinite(seconds) || seconds <= 0) return;
        parts.duration = seconds;
        parts.wave.setAttribute('aria-valuemax', String(Math.floor(seconds)));
        updatePosition(parts, parts.audio ? parts.audio.currentTime || 0 : 0);
    }

    // The note playing now, so starting another can pause it.
    let playing = null;

    function showPlaying(parts, isPlaying) {
        parts.root.classList.toggle('is-playing', isPlaying);
        parts.play.setAttribute('aria-label', isPlaying ? 'Pause voice message' : 'Play voice message');
        const glyph = parts.play.children[0];
        if (glyph && window.revealxIcons && glyph.children) {
            window.revealxIcons.setIcon(glyph, isPlaying ? 'pause' : 'play');
        }
    }

    /**
     * Wire a built player to an <audio> element. `options.duration` is the
     * length if already known (the decoded audio's), else the element's own
     * is used once it reports a finite one.
     */
    function attachVoicePlayer(parts, audio, options = {}) {
        parts.audio = audio;
        setDuration(parts, options.duration);

        parts.play.addEventListener('click', () => {
            if (audio.paused) {
                if (playing && playing !== parts && playing.audio) playing.audio.pause();
                playing = parts;
                const started = audio.play();
                if (started && typeof started.catch === 'function') started.catch(() => {});
            } else {
                audio.pause();
            }
        });

        parts.speed.addEventListener('click', () => {
            const rate = nextSpeed(audio.playbackRate || 1);
            audio.playbackRate = rate;
            parts.speed.textContent = speedLabel(rate);
            parts.speed.setAttribute('aria-label', `Playback speed ${rate} times, change`);
        });

        const seekTo = (seconds) => {
            if (!(parts.duration > 0)) return;
            audio.currentTime = seconds;
            updatePosition(parts, seconds);
        };

        parts.wave.addEventListener('keydown', (event) => {
            const target = seekFromKey(event.key, audio.currentTime || 0, parts.duration || 0);
            if (target === null) return;
            event.preventDefault();
            seekTo(target);
        });

        parts.wave.addEventListener('click', (event) => {
            if (!event || typeof parts.wave.getBoundingClientRect !== 'function') return;
            const box = parts.wave.getBoundingClientRect();
            if (!box.width) return;
            const fraction = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
            seekTo(fraction * parts.duration);
        });

        audio.addEventListener('play', () => showPlaying(parts, true));
        audio.addEventListener('pause', () => showPlaying(parts, false));
        audio.addEventListener('timeupdate', () => updatePosition(parts, audio.currentTime || 0));
        audio.addEventListener('loadedmetadata', () => {
            if (!(parts.duration > 0)) setDuration(parts, audio.duration);
        });
        audio.addEventListener('durationchange', () => {
            if (!(parts.duration > 0)) setDuration(parts, audio.duration);
        });
        audio.addEventListener('ended', () => {
            audio.currentTime = 0;
            if (playing === parts) playing = null;
            showPlaying(parts, false);
            updatePosition(parts, 0);
        });
        return parts;
    }

    // ------------------------------------------------------ recording meter

    /** Loudness 0..1 from an AnalyserNode's getByteTimeDomainData buffer. */
    function levelFromTimeDomain(bytes) {
        if (!bytes || !bytes.length) return 0;
        let sum = 0;
        for (let i = 0; i < bytes.length; i += 1) {
            const centred = (bytes[i] - 128) / 128;
            sum += centred * centred;
        }
        // Speech sits far below full scale; this gain makes it fill the meter.
        return Math.min(1, Math.sqrt(sum / bytes.length) * 2.2);
    }

    /**
     * A row of bars that scrolls left as levels are pushed on the right:
     * the shape of what is being recorded, as it is recorded. Decorative --
     * the clock and the announced state carry the meaning.
     */
    function buildLevelMeter(doc, count = 28) {
        const root = doc.createElement('span');
        root.className = 'voice-levels';
        root.setAttribute('aria-hidden', 'true');
        const bars = [];
        const values = new Array(count).fill(0);
        for (let i = 0; i < count; i += 1) {
            const bar = doc.createElement('span');
            bar.className = 'voice-bar';
            setLevel(bar, 0);
            root.append(bar);
            bars.push(bar);
        }
        return {
            root,
            bars,
            push(level) {
                values.shift();
                values.push(level);
                bars.forEach((bar, i) => setLevel(bar, values[i]));
            },
            reset() {
                values.fill(0);
                bars.forEach((bar) => setLevel(bar, 0));
            },
        };
    }

    window.revealxVoiceUI = {
        formatClock,
        peaksFromSamples,
        placeholderPeaks,
        levelClass,
        seekFromKey,
        nextSpeed,
        buildVoicePlayer,
        setPeaks,
        setDuration,
        attachVoicePlayer,
        levelFromTimeDomain,
        buildLevelMeter,
    };
}());

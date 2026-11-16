// The reconstruction panel: download, verify, XOR.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import { shared } from './shared.js';
import {
    closePanel,
    downloadReconstructedBtn,
    isClientSplitShare,
    openShareEnvelope,
    reconstructBtn,
    reconstructionPanel,
    state,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';
import {
    renderTamperPanel,
    revealShareImage,
    runLocalShareVerification,
    runShareAnalysis,
    tamperPanelFor,
} from './render.js';

registerLate({ blobToDataUrl, openReconstruction });

// ==========================================
// 13. Visual Cryptography Local Reconstruction
// ==========================================

/**
 * Switch the reconstruction panel between its two entry points.
 *
 * 'message' - opened from a share bubble; both shares load themselves.
 * 'manual'  - opened from the sidebar; the user supplies two files.
 */
export function setReconstructionMode(mode) {
    reconstructionPanel.classList.toggle('manual-mode', mode === 'manual');
}

function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('Could not read Share 1'));
        reader.readAsDataURL(blob);
    });
}

/**
 * Open the reconstruction panel for a share message.
 *
 * Share 1 has one-time access, so it is downloaded exactly once here and the
 * resulting data URL is reused for both the preview and the XOR - fetching it
 * again (as an <img src> would) burns the access and returns 404.
 */
// Why Share 2 could not be used, by openMessage's status.
const SHARE_ENVELOPE_NOTICES = {
    legacy: 'This image was sent before an update; it can\'t be opened.',
    unverifiable: 'This image can\'t be verified - it was sent more than an hour ago or with a clock far from this one.',
    replay: 'This image was already delivered once, so this copy is not opened.',
};

async function openReconstruction(msg) {
    const share1Url = msg && msg.extra ? msg.extra.share1_url : null;
    let envelope = null;
    let envelopeStatus = 'failed';
    if (isClientSplitShare(msg)) {
        try {
            ({ envelope, status: envelopeStatus } = await openShareEnvelope(msg));
        } catch (error) {
            console.error('Share envelope could not be opened', error);
        }
    }
    const share2Live = envelope
        ? `data:image/png;base64,${envelope.share2_png_b64}`
        : (msg && msg.extra ? msg.extra.share2_live : null);

    const p1 = document.getElementById('share1Preview');
    const p2 = document.getElementById('share2Preview');
    const recPrev = document.getElementById('reconstructedPreview');
    const warning = document.getElementById('reconstructionWarning');

    if (p1) p1.src = '';
    if (p2) p2.src = share2Live || '';
    if (recPrev) recPrev.src = '';
    downloadReconstructedBtn.disabled = true;
    downloadReconstructedBtn.dataset.image = '';
    shared.currentShares = { share1Url: null, share2Live, envelope, share1Bytes: null, messageId: msg ? msg.id : null };

    // Opened from a message: the shares arrive on their own, so no file pickers.
    if (isClientSplitShare(msg) && !envelope) {
        reconstructionPanel.classList.add('active');
        reconstructBtn.disabled = true;
        if (warning) {
            warning.textContent = SHARE_ENVELOPE_NOTICES[envelopeStatus]
                || 'Share 2 could not be decrypted with the current keys, so this image cannot be reconstructed.';
            warning.className = 'warning-text danger';
        }
        return;
    }
    setReconstructionMode('message');
    setShareDownload(downloadShare1Btn, null);
    setShareDownload(downloadShare2Btn, share2Live);

    reconstructionPanel.classList.add('active');
    reconstructBtn.disabled = true;
    announceToScreenReader('Visual cryptography reconstruction panel opened.');

    if (!share1Url) {
        if (warning) warning.textContent = 'Select both shares manually to reconstruct.';
        reconstructBtn.disabled = false;
        return;
    }

    // Surface the tamper verdict here too, so the warning is in front of the
    // user at the moment they are about to open the image.
    //
    // Deliberately forced rather than reusing the verdict cached when the
    // bubble first rendered. That one could be minutes old, and the share may
    // have been altered on the server since. It also has to happen *now*,
    // before the fetch below spends the one-time token: afterwards the server
    // will refuse to look at the share again and no second opinion is possible.
    const analysis = await runShareAnalysis(msg, { force: true });

    if (warning) warning.textContent = 'Fetching Share 1 (one-time access)...';
    try {
        const response = await fetch(share1Url);
        if (!response.ok) {
            throw new Error('Share 1 is no longer available. It may have been opened already, revoked, or expired.');
        }
        const blob = await response.blob();
        const dataUrl = await blobToDataUrl(blob);
        if (p1) p1.src = dataUrl;
        shared.currentShares.share1Url = dataUrl;
        if (envelope) {
            // Hash the exact bytes that arrived, before anything decodes them.
            const share1Bytes = new Uint8Array(await blob.arrayBuffer());
            shared.currentShares.share1Bytes = share1Bytes;
            const actual = await window.revealxVC.sha256Hex(share1Bytes);
            state.endToEndHash[msg.id] = {
                matches: actual === envelope.share1_sha256,
                expected: envelope.share1_sha256,
                actual,
            };
        }
        setShareDownload(downloadShare1Btn, dataUrl);
        reconstructBtn.disabled = false;

        // Now that the bytes are actually in the browser, re-run the model here
        // on those exact bytes. This is the only check in the flow that does
        // not require trusting the server's report about its own storage.
        if (warning) warning.textContent = 'Share 1 loaded. Verifying in-browser...';
        const local = await runLocalShareVerification(msg.id, dataUrl, tamperPanelFor(msg.id));

        const endToEnd = state.endToEndHash[msg.id];
        if (endToEnd && analysis) renderTamperPanel(tamperPanelFor(msg.id), analysis, local);
        if (warning && endToEnd && !endToEnd.matches) {
            warning.textContent = 'Share 1 is not the one the sender made: its SHA-256 does not '
                + "match the hash sealed in the sender's encrypted message. "
                + 'The reconstruction below will be wrong.';
            warning.className = 'warning-text danger';
        } else if (warning) {
            const localTampered = local ? local.tampered : false;
            if (!analysis && !local) {
                warning.textContent = 'Share 1 loaded. Integrity could not be checked.';
                warning.className = 'warning-text';
            } else if ((analysis && (analysis.integrity.exact_tamper_detected || analysis.ml.tampered))
                       || localTampered) {
                const parts = [];
                if (analysis) {
                    parts.push(`HMAC ${analysis.integrity.hmac_match ? 'matched' : 'mismatch'}`);
                    parts.push(`server ML ${(analysis.ml.confidence * 100).toFixed(1)}%`);
                }
                if (local) {
                    parts.push(`in-browser ML ${(local.confidence * 100).toFixed(1)}%`);
                }
                warning.textContent = `Tampering detected (${parts.join(', ')}). `
                    + 'The reconstruction below will be damaged.';
                warning.className = 'warning-text danger';
            } else {
                warning.textContent = local
                    ? 'Share 1 verified intact, confirmed independently in your browser. Safe to reconstruct.'
                    : 'Share 1 verified intact. Safe to reconstruct.';
                warning.className = 'warning-text success';
            }
        }
    } catch (error) {
        if (warning) {
            warning.textContent = error.message;
            warning.className = 'warning-text danger';
        }
    }
}

closePanel.addEventListener('click', () => {
    reconstructionPanel.classList.remove('active');
});

reconstructBtn.addEventListener('click', async () => {
    if (!shared.currentShares || !shared.currentShares.share1Url || !shared.currentShares.share2Live) {
        window.revealxDialogs.toast('Both Share 1 and Share 2 are needed to rebuild the image.', { kind: 'error' });
        return;
    }

    const spinner = document.getElementById('reconstructBtnSpinner');
    if (spinner) spinner.classList.remove('hidden');

    try {
        if (shared.currentShares.envelope && shared.currentShares.share1Bytes) {
            // Browser-split share: XOR the decoded bytes exactly, rather than
            // whatever a canvas returns for them.
            const result = await window.revealxVC.verifyAndReconstruct(
                shared.currentShares.share1Bytes, shared.currentShares.envelope);
            const image = await window.revealxVC.pngDataUrl(result.pixels, result.width, result.height);
            const preview = document.getElementById('reconstructedPreview');
            if (preview) preview.src = image;
            downloadReconstructedBtn.disabled = false;
            downloadReconstructedBtn.dataset.image = image;
            // The bubble shows the picture too, so the chat reads as a chat.
            revealShareImage(shared.currentShares.messageId, image);
        } else {
            // Manually supplied shares, and shares from the old server split.
            await window.reconstructSharesLocally(shared.currentShares.share1Url, shared.currentShares.share2Live);
        }
    } catch (e) {
        console.error(e);
    } finally {
        if (spinner) spinner.classList.add('hidden');
    }
});

// Manual share uploads. These controls are hidden unless the panel was opened
// in manual mode; see setReconstructionMode().
const selectShare1Btn = document.getElementById('selectShare1Btn');
const selectShare2Btn = document.getElementById('selectShare2Btn');
const share1Input = document.getElementById('share1Input');
const share2Input = document.getElementById('share2Input');
export const share1Preview = document.getElementById('share1Preview');
export const share2Preview = document.getElementById('share2Preview');
export const downloadShare1Btn = document.getElementById('downloadShare1Btn');
export const downloadShare2Btn = document.getElementById('downloadShare2Btn');

if (selectShare1Btn) selectShare1Btn.addEventListener('click', () => share1Input.click());
if (selectShare2Btn) selectShare2Btn.addEventListener('click', () => share2Input.click());

function readSharePick(input, preview, downloadBtn, assign) {
    if (!input) return;
    input.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (file && file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const dataUrl = e.target.result;
                if (preview) preview.src = dataUrl;
                if (!shared.currentShares) shared.currentShares = { share1Url: null, share2Live: null };
                assign(dataUrl);
                setShareDownload(downloadBtn, dataUrl);
            };
            reader.readAsDataURL(file);
        }
        input.value = '';
    });
}

readSharePick(share1Input, share1Preview, downloadShare1Btn, url => { shared.currentShares.share1Url = url; });
readSharePick(share2Input, share2Preview, downloadShare2Btn, url => { shared.currentShares.share2Live = url; });

/** Arm (or disable) a download button for a share image. */
export function setShareDownload(button, dataUrl) {
    if (!button) return;
    button.disabled = !dataUrl;
    button.dataset.image = dataUrl || '';
}

function downloadDataUrl(dataUrl, filename) {
    if (!dataUrl) return;
    const link = document.createElement('a');
    link.href = dataUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
}

// The recipient legitimately holds both shares, so let them save either one.
if (downloadShare1Btn) {
    downloadShare1Btn.addEventListener('click', () =>
        downloadDataUrl(downloadShare1Btn.dataset.image, 'share1.png'));
}
if (downloadShare2Btn) {
    downloadShare2Btn.addEventListener('click', () =>
        downloadDataUrl(downloadShare2Btn.dataset.image, 'share2.png'));
}

downloadReconstructedBtn.addEventListener('click', () =>
    downloadDataUrl(downloadReconstructedBtn.dataset.image, 'reconstructed.png'));


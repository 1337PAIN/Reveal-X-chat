// Picking, downscaling and sending an image share.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import {
    cancelImageBtn,
    confirmImageBtn,
    imageInput,
    imagePreview,
    imagePreviewMeta,
    imagePreviewModal,
    shareImageBtn,
    socket,
    state,
} from './core.js';
import {
    checkPeerPin,
    openSafetyChangeDialog,
    peerPublicKey,
} from './keys.js';
import {
    showTransientError,
} from './reactions.js';
import {
    connectionReady,
} from './messaging.js';

registerLate({ closeImagePreview });

// ==========================================
// 12. Local Image Downscale Compression
// ==========================================

shareImageBtn.addEventListener('click', () => {
    if (!state.selectedRecipientId) {
        window.revealxDialogs.toast('Select an account first.');
        return;
    }
    imageInput.click();
});

imageInput.addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file || !state.selectedRecipientId) return;

    if (!file.type.startsWith('image/')) {
        window.revealxDialogs.toast('Choose an image file.', { kind: 'error' });
        return;
    }

    // Downscale and compress image client side (max width 1200px)
    const reader = new FileReader();
    reader.onload = (e) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement('canvas');
            let w = img.width;
            let h = img.height;
            const max = 1200;
            if (w > max || h > max) {
                if (w > h) {
                    h = Math.round(h * (max / w));
                    w = max;
                } else {
                    w = Math.round(w * (max / h));
                    h = max;
                }
            }
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, w, h);
            
            // Compress JPEG at 85% quality
            const compressedUrl = canvas.toDataURL('image/jpeg', 0.85);
            state.pendingImage = {
                name: file.name,
                size: file.size,
                dataUrl: compressedUrl
            };
            
            imagePreview.src = compressedUrl;
            // What will actually be split: the downscaled size, not the file's.
            imagePreviewMeta.textContent = window.revealxShareUI.previewMeta({
                name: file.name,
                width: w,
                height: h,
                bytes: window.revealxShareUI.dataUrlBytes(compressedUrl),
            });
            imagePreviewModal.classList.remove('hidden');
        };
        img.src = e.target.result;
    };
    reader.readAsDataURL(file);
    imageInput.value = '';
});

cancelImageBtn.addEventListener('click', closeImagePreview);
/**
 * Split the image here, upload Share 1, and seal Share 2 for the recipient.
 *
 * The image itself used to be uploaded and split on the server, which then
 * held every picture anyone sent. Now nothing that leaves this browser is the
 * image: Share 1 is random, and Share 2 is inside an E2EE v4 message. That
 * needs both sides to have keys -- there is no unencrypted way to send Share 2
 * that would not hand the server the picture -- so without them this refuses.
 */
async function sendImageShare(recipientId, dataUrl) {
    if (!connectionReady()) return false;
    const pin = await checkPeerPin(recipientId);
    if (pin.status === 'changed') {
        openSafetyChangeDialog(recipientId);
        return false;
    }
    const peerKey = peerPublicKey(recipientId);
    if (!window.revealxE2EE.isReady() || !peerKey) {
        showTransientError('Images are end-to-end encrypted, so both of you need encryption keys. '
            + 'This contact has not set one up yet.');
        return false;
    }

    const { gray, width, height } = await window.revealxVC.imageToGray(dataUrl);
    const prepared = await window.revealxVC.prepareShare(gray, width, height);
    let sealed;
    try {
        sealed = await window.revealxE2EE.encrypt(peerKey, prepared.envelope, { recipientId });
    } catch (error) {
        sealed = error;
    }
    if (window.revealxSendPolicy.afterEncrypt(sealed).action !== 'send') {
        console.error('Share encryption failed; nothing was sent', sealed);
        showTransientError('The image could not be encrypted, so it was not sent.');
        return false;
    }
    socket.emit('send_share', {
        recipient_id: recipientId,
        share1_png: window.revealxVC.bytesToBase64(prepared.share1Png),
        envelope: sealed.ciphertext,
        iv: sealed.iv,
        encrypted: true,
        v: sealed.v,
        msg_uuid: sealed.msg_uuid,
        sent_at: sealed.sent_at,
        epk: sealed.epk,
    });
    return true;
}

confirmImageBtn.addEventListener('click', async () => {
    if (!state.pendingImage || !state.selectedRecipientId) return;

    confirmImageBtn.disabled = true;
    confirmImageBtn.textContent = 'Splitting...';
    try {
        await sendImageShare(state.selectedRecipientId, state.pendingImage.dataUrl);
    } catch (error) {
        console.error('Image share failed', error);
        showTransientError('The image could not be prepared for sending.');
    }
    closeImagePreview();
});

function closeImagePreview() {
    imagePreviewModal.classList.add('hidden');
    imagePreview.src = '';
    state.pendingImage = null;
    confirmImageBtn.disabled = false;
    confirmImageBtn.textContent = 'Split & Send';
}


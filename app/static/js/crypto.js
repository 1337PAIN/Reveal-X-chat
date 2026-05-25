/**
 * Client-side Visual Cryptography operations
 * [NEW] Extracted for modularity and security audit
 */

/**
 * Reconstruct image from two shares using XOR with UI state management
 * @param {string} share1Url - URL or data URL for share 1
 * @param {string} share2Live - data URL for share 2
 * @returns {Promise<string>} Promise resolving to reconstructed image data URL
 */
async function reconstructSharesLocally(share1Url, share2Live) {
    const reconstructBtn = document.getElementById('reconstructBtn');
    const downloadReconstructedBtn = document.getElementById('downloadReconstructedBtn');
    const reconstructedPreview = document.getElementById('reconstructedPreview');
    
    try {
        if (reconstructBtn) {
            reconstructBtn.disabled = true;
            const reconstructBtnText = reconstructBtn.querySelector('#reconstructBtnText');
            if (reconstructBtnText) reconstructBtnText.textContent = 'Reconstructing...';
        }

        const [share1Image, share2Image] = await Promise.all([
            loadImageForCanvas(share1Url),
            loadImageForCanvas(share2Live),
        ]);

        if (share1Image.width !== share2Image.width || share1Image.height !== share2Image.height) {
            throw new Error('Shares must have the same dimensions');
        }

        const width = share1Image.width;
        const height = share1Image.height;
        const canvas1 = drawToCanvas(share1Image, width, height);
        const canvas2 = drawToCanvas(share2Image, width, height);
        const outputCanvas = document.createElement('canvas');
        outputCanvas.width = width;
        outputCanvas.height = height;

        const context1 = canvas1.getContext('2d');
        const context2 = canvas2.getContext('2d');
        const outputContext = outputCanvas.getContext('2d');
        const imageData1 = context1.getImageData(0, 0, width, height);
        const imageData2 = context2.getImageData(0, 0, width, height);
        const outputData = outputContext.createImageData(width, height);

        for (let index = 0; index < imageData1.data.length; index += 4) {
            const pixel = imageData1.data[index] ^ imageData2.data[index];
            outputData.data[index] = pixel;
            outputData.data[index + 1] = pixel;
            outputData.data[index + 2] = pixel;
            outputData.data[index + 3] = 255;
        }

        outputContext.putImageData(outputData, 0, 0);
        const reconstructed = outputCanvas.toDataURL('image/png');
        
        if (reconstructedPreview) {
            reconstructedPreview.src = reconstructed;
        }
        if (downloadReconstructedBtn) {
            downloadReconstructedBtn.disabled = false;
            downloadReconstructedBtn.dataset.image = reconstructed;
        }
        
        return reconstructed;
    } catch (error) {
        alert('Error: ' + error.message);
        throw error;
    } finally {
        if (reconstructBtn) {
            reconstructBtn.disabled = false;
            const reconstructBtnText = reconstructBtn.querySelector('#reconstructBtnText');
            if (reconstructBtnText) reconstructBtnText.textContent = 'Reconstruct Image';
        }
    }
}

/**
 * Load image from URL for canvas manipulation
 * @param {string} src - Image URL or data URL
 * @returns {Promise<Image>} Promise resolving to Image object
 */
function loadImageForCanvas(src) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('Could not load image share'));
        image.src = src;
    });
}

/**
 * Draw image to canvas
 * @param {Image} image - Image object
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @returns {HTMLCanvasElement} Canvas with image drawn
 */
function drawToCanvas(image, width, height) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    context.drawImage(image, 0, 0);
    return canvas;
}

// Expose to window for use in chat.js
window.reconstructSharesLocally = reconstructSharesLocally;
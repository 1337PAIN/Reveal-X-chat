const input = document.getElementById('imageInput');
const dropZone = document.getElementById('dropZone');
const runBtn = document.getElementById('runBtn');
const sourcePreview = document.getElementById('sourcePreview');
const statusText = document.getElementById('statusText');
const attackSelect = document.getElementById('attackSelect');
const strengthRange = document.getElementById('strengthRange');
const strengthValue = document.getElementById('strengthValue');
let selectedDataUrl = '';

const ids = {
    original: 'imgOriginal',
    share1_clean: 'imgShare1Clean',
    share1_received: 'imgShare1Received',
    share2: 'imgShare2',
    reconstructed: 'imgReconstructed',
    enhanced: 'imgEnhanced',
    heatmap: 'imgHeatmap'
};

strengthRange.addEventListener('input', () => {
    strengthValue.textContent = `${strengthRange.value}%`;
});

dropZone.addEventListener('click', () => input.click());
dropZone.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropZone.style.borderColor = 'var(--accent)';
});
dropZone.addEventListener('dragleave', () => {
    dropZone.style.borderColor = 'rgba(94, 234, 212, .45)';
});
dropZone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropZone.style.borderColor = 'rgba(94, 234, 212, .45)';
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) loadFile(file);
});
input.addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) loadFile(file);
});

function loadFile(file) {
    if (!file.type.startsWith('image/')) {
        statusText.textContent = 'Please choose a valid image file.';
        return;
    }
    const reader = new FileReader();
    reader.onload = () => {
        selectedDataUrl = reader.result;
        sourcePreview.src = selectedDataUrl;
        sourcePreview.style.display = 'block';
        runBtn.disabled = false;
        statusText.textContent = `${file.name} loaded. Choose attack type and run analysis.`;
    };
    reader.readAsDataURL(file);
}

runBtn.addEventListener('click', async () => {
    if (!selectedDataUrl) return;
    setLoading(true);
    try {
        const res = await fetch('/api/lab/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                image: selectedDataUrl,
                attack: attackSelect.value,
                strength: Number(strengthRange.value) / 100
            })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Processing failed');
        renderResults(data);
        statusText.textContent = 'Analysis completed.';
        // Deliberately after the server results are on screen: the browser run
        // is a cross-check, and awaiting it first would make the whole page
        // wait on an 11 MB wasm download the first time.
        runBrowserDetection(data);
    } catch (error) {
        statusText.textContent = error.message;
    } finally {
        setLoading(false);
    }
});

/**
 * Re-run the same model in the browser and report whether it agrees.
 *
 * The point is not a second opinion for its own sake -- it is to show that the
 * detector does not need the server at all. The exported ONNX graph is the same
 * forest scikit-learn fitted, and the 13 features are recomputed here from the
 * share's pixels. /lab/parity pins the two implementations against each other.
 */
async function runBrowserDetection(data) {
    const result = document.getElementById('onnxResult');
    const detail = document.getElementById('onnxDetail');
    if (!result || !window.revealxTamperML) return;

    const share = data.images && data.images.share1_received;
    if (!share) return;

    result.textContent = 'Loading…';
    detail.textContent = 'Fetching onnxruntime-web (first run only)…';

    const local = await window.revealxTamperML.analyse(share, { heatmap: false });
    const card = result.parentElement;

    if (!local) {
        result.textContent = 'Unavailable';
        detail.textContent = window.revealxTamperML.error
            ? `Runtime unavailable: ${window.revealxTamperML.error}`
            : (window.revealxTamperML.lastSkipReason || 'Declined this input.');
        card.classList.remove('danger', 'success');
        return;
    }

    card.classList.toggle('danger', local.tampered);
    card.classList.toggle('success', !local.tampered);
    result.textContent = local.label;

    const agrees = local.tampered === data.ml.tampered;
    const delta = Math.abs(local.probability_tampered - data.ml.probability_tampered);
    detail.textContent = `${(local.confidence * 100).toFixed(1)}% confidence in `
        + `${local.timing_ms.inference} ms (features ${local.timing_ms.features} ms) • `
        + (agrees
            ? `agrees with server, Δp = ${delta.toExponential(1)}`
            : 'DISAGREES with the server verdict');
}

function setLoading(isLoading) {
    runBtn.disabled = isLoading || !selectedDataUrl;
    runBtn.textContent = isLoading ? 'Processing...' : 'Run Reveal-X Analysis';
    if (isLoading) statusText.textContent = 'Generating shares, running ML detection and calculating metrics...';
}

function renderResults(data) {
    Object.entries(ids).forEach(([key, elementId]) => {
        const el = document.getElementById(elementId);
        if (el && data.images[key]) el.src = data.images[key];
    });

    const integrityCard = document.getElementById('integrityResult').parentElement;
    const mlCard = document.getElementById('mlResult').parentElement;
    integrityCard.classList.toggle('danger', data.integrity.exact_tamper_detected);
    integrityCard.classList.toggle('success', !data.integrity.exact_tamper_detected);
    mlCard.classList.toggle('danger', data.ml.tampered);
    mlCard.classList.toggle('success', !data.ml.tampered);

    document.getElementById('integrityResult').textContent = data.integrity.exact_tamper_detected ? 'Tampered' : 'Clean';
    document.getElementById('integrityDetail').textContent = data.integrity.hmac_match
        ? 'HMAC matched: exact cryptographic integrity passed.'
        : 'HMAC mismatch: received Share 1 changed after generation.';

    document.getElementById('mlResult').textContent = `${data.ml.label}`;
    document.getElementById('mlDetail').textContent = `${data.ml.model_name} • confidence ${(data.ml.confidence * 100).toFixed(1)}%`;

    document.getElementById('qualityResult').textContent = `${data.metrics.reconstructed.psnr} dB / ${data.metrics.reconstructed.ssim}`;
    document.getElementById('qualityDetail').textContent = data.metrics.quality_safe_fallback
        ? `Enhancement lowered SSIM (${data.metrics.enhanced_candidate.ssim}), so the plain reconstruction is reported instead.`
        : `Enhanced: ${data.metrics.enhanced.psnr} dB / ${data.metrics.enhanced.ssim}`;
    document.getElementById('timeResult').textContent = `${data.metrics.processing_ms} ms`;

    document.getElementById('mseReconstructed').textContent = data.metrics.reconstructed.mse;
    document.getElementById('psnrReconstructed').textContent = `${data.metrics.reconstructed.psnr} dB`;
    document.getElementById('ssimReconstructed').textContent = data.metrics.reconstructed.ssim;
    const fallbackNote = data.metrics.quality_safe_fallback ? ' *' : '';
    document.getElementById('mseEnhanced').textContent = `${data.metrics.enhanced.mse}${fallbackNote}`;
    document.getElementById('psnrEnhanced').textContent = `${data.metrics.enhanced.psnr} dB${fallbackNote}`;
    document.getElementById('ssimEnhanced').textContent = `${data.metrics.enhanced.ssim}${fallbackNote}`;

    const fallbackEl = document.getElementById('fallbackNote');
    if (fallbackEl) {
        fallbackEl.textContent = data.metrics.quality_safe_fallback
            ? '* Enhancement was skipped for this run: it scored SSIM ' +
              `${data.metrics.enhanced_candidate.ssim} against ${data.metrics.reconstructed.ssim} for the raw reconstruction, ` +
              'so the unenhanced image is reported.'
            : '';
    }

    const list = document.getElementById('explanationList');
    list.innerHTML = '';
    data.ml.explanation.forEach((line) => {
        const li = document.createElement('li');
        li.textContent = line;
        list.appendChild(li);
    });

    document.getElementById('hashBlock').textContent = [
        `Attack: ${data.attack}`,
        `SHA-256 original Share 1: ${data.integrity.sha256_original_share1}`,
        `SHA-256 received Share 1: ${data.integrity.sha256_received_share1}`,
        `HMAC match: ${data.integrity.hmac_match}`,
        `ML probability tampered: ${(data.ml.probability_tampered * 100).toFixed(2)}%`
    ].join('\n');
}

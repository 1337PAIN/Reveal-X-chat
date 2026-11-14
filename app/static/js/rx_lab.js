const input = document.getElementById('imageInput');
const dropZone = document.getElementById('dropZone');
const runBtn = document.getElementById('runBtn');
const sourcePreview = document.getElementById('sourcePreview');
const statusText = document.getElementById('statusText');
const attackSelect = document.getElementById('attackSelect');
const strengthRange = document.getElementById('strengthRange');
const strengthValue = document.getElementById('strengthValue');
const runAllBtn = document.getElementById('runAllBtn');
const sweepBtn = document.getElementById('sweepBtn');
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
    dropZone.classList.add('dragging');
});
dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragging');
});
dropZone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropZone.classList.remove('dragging');
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
        if (runAllBtn) runAllBtn.disabled = false;
        if (sweepBtn) sweepBtn.disabled = false;
        statusText.textContent = `${file.name} loaded. Choose attack type and run analysis.`;
    };
    reader.readAsDataURL(file);
}

/**
 * One /api/lab/process call. A rate-limit answer (429, "Try again in Ns") is
 * waited out and retried, so a long run carries on instead of failing
 * halfway; anything else is thrown.
 */
async function processOnce(attack, strength) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
        const res = await fetch('/api/lab/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: selectedDataUrl, attack, strength }),
        });
        const data = await res.json();
        if (res.ok && data.ok) return data;
        const wait = res.status === 429 ? window.revealxLabFormat.retryAfter(data.error) : null;
        if (wait === null) throw new Error(data.error || 'Processing failed');
        statusText.textContent = `Waiting ${wait}s for the lab's rate limit...`;
        await new Promise((resolve) => setTimeout(resolve, (wait + 1) * 1000));
    }
    throw new Error('Still rate limited after waiting. Try again in a minute.');
}

function tableCell(text, header) {
    const cell = document.createElement(header ? 'th' : 'td');
    if (header) cell.setAttribute('scope', 'row');
    cell.textContent = text;
    return cell;
}

/**
 * Every attack on the uploaded image, in the README's order, one request at
 * a time (the lab allows 20 a minute; seven fit), filling the table as each
 * comes back. Built with textContent throughout.
 */
async function runAllAttacks() {
    const { ATTACKS, attackRow } = window.revealxLabFormat;
    const body = document.getElementById('attackTableBody');
    const note = document.getElementById('attackTableNote');
    const strength = Number(strengthRange.value) / 100;
    body.replaceChildren();
    note.textContent = '';
    const missed = [];
    let flaggedByMl = 0;
    for (const [index, attack] of ATTACKS.entries()) {
        statusText.textContent = `Running ${index + 1} of ${ATTACKS.length}: ${attack.label}...`;
        const row = attackRow(attack.label, await processOnce(attack.value, strength));
        const tr = document.createElement('tr');
        tr.append(
            tableCell(row.attack, true),
            tableCell(row.missedByMl ? 'no (missed)' : row.ml),
            tableCell(row.hmac),
            tableCell(row.psnr),
            tableCell(row.ssim),
        );
        if (row.ml === 'yes') flaggedByMl += 1;
        if (row.missedByMl) {
            tr.classList.add('missed');
            missed.push(row.attack);
        }
        body.append(tr);
    }
    // Only what the rows show: "missed" means the HMAC caught it and the ML
    // did not; the ML's own count is reported separately.
    note.textContent = missed.length
        ? `The ML flagged ${flaggedByMl} of ${ATTACKS.length}; it missed ${missed.join(', ')}, which the HMAC caught.`
        : `The ML flagged ${flaggedByMl} of ${ATTACKS.length} at this strength.`;
    statusText.textContent = `All ${ATTACKS.length} attacks done.`;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs, text) {
    const el = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([name, value]) => el.setAttribute(name, String(value)));
    if (text !== undefined) el.textContent = text;
    return el;
}

/**
 * Draw the sweep from sweepChart()'s geometry: axes, 0/0.5/1 gridlines, a
 * solid SSIM line with circles and a dashed ML line with squares, so the
 * two differ by more than colour. Built with createElementNS throughout.
 */
function drawSweep(points) {
    const svg = document.getElementById('sweepChart');
    // Drawn at the chart's real width, not scaled from a fixed viewBox:
    // scaled down to a phone, 12px labels came out at about 6px.
    const width = Math.max(300, Math.round(svg.getBoundingClientRect().width) || 640);
    const box = { width, height: 280, pad: { top: 16, right: 20, bottom: 40, left: 48 } };
    svg.setAttribute('viewBox', `0 0 ${width} ${box.height}`);
    const chart = window.revealxLabFormat.sweepChart(points, box);
    const bottom = box.height - box.pad.bottom;
    const right = box.width - box.pad.right;
    const parts = [];
    chart.yTicks.forEach((tick) => {
        parts.push(svgEl('line', { x1: box.pad.left, x2: right, y1: tick.y, y2: tick.y, class: 'sweep-grid' }));
        parts.push(svgEl('text', { x: box.pad.left - 8, y: tick.y + 4, 'text-anchor': 'end', class: 'sweep-label' }, tick.label));
    });
    chart.xTicks.forEach((tick) => {
        parts.push(svgEl('text', { x: tick.x, y: bottom + 20, 'text-anchor': 'middle', class: 'sweep-label' }, tick.label));
    });
    parts.push(svgEl('text', { x: (box.pad.left + right) / 2, y: box.height - 4, 'text-anchor': 'middle', class: 'sweep-label' }, 'Attack strength'));
    parts.push(svgEl('path', { d: chart.ssimPath, class: 'sweep-line sweep-ssim' }));
    parts.push(svgEl('path', { d: chart.mlPath, class: 'sweep-line sweep-ml' }));
    chart.points.forEach((p) => {
        parts.push(svgEl('circle', { cx: p.x, cy: p.ssimY, r: 4.5, class: 'sweep-dot sweep-ssim-dot' }));
        parts.push(svgEl('rect', { x: p.x - 4, y: p.mlY - 4, width: 8, height: 8, class: 'sweep-dot sweep-ml-dot' }));
    });
    svg.replaceChildren(...parts);
}

// Redrawn at the new width when the window changes size (a phone rotating),
// for the same reason it is drawn at its real width in the first place.
let lastSweep = null;
let resizeQueued = false;
window.addEventListener('resize', () => {
    if (!lastSweep || resizeQueued) return;
    resizeQueued = true;
    setTimeout(() => { resizeQueued = false; drawSweep(lastSweep); }, 150);
});

/** The chosen attack at each of SWEEP_STRENGTHS, one request at a time. */
async function runSweep() {
    const { SWEEP_STRENGTHS, metric, sweepSummary } = window.revealxLabFormat;
    // "None" has no strength to sweep; block replacement stands in for it.
    const attack = attackSelect.value === 'none' ? 'block' : attackSelect.value;
    const label = attackSelect.querySelector(`option[value="${attack}"]`).textContent;
    const body = document.getElementById('sweepTableBody');
    const points = [];
    body.replaceChildren();
    for (const [index, strength] of SWEEP_STRENGTHS.entries()) {
        statusText.textContent = `Sweeping ${label}: ${index + 1} of ${SWEEP_STRENGTHS.length} (${Math.round(strength * 100)}%)...`;
        const data = await processOnce(attack, strength);
        const point = {
            strength,
            ssim: data.metrics.reconstructed.ssim,
            ml: data.ml.probability_tampered,
            verdict: data.ml.tampered ? 'Tampered' : 'Clean',
        };
        points.push(point);
        const tr = document.createElement('tr');
        tr.append(
            tableCell(`${Math.round(strength * 100)}%`, true),
            tableCell(metric(point.ssim)),
            tableCell(metric(point.ml)),
            tableCell(point.verdict),
        );
        body.append(tr);
    }
    lastSweep = points;
    drawSweep(points);
    document.getElementById('sweepSummary').textContent = sweepSummary(label, points);
    statusText.textContent = `Sweep of ${label} done.`;
}

if (sweepBtn) {
    sweepBtn.addEventListener('click', async () => {
        if (!selectedDataUrl) return;
        [sweepBtn, runAllBtn, runBtn].forEach((b) => { if (b) b.disabled = true; });
        try {
            await runSweep();
        } catch (error) {
            statusText.textContent = error.message;
        } finally {
            [sweepBtn, runAllBtn, runBtn].forEach((b) => { if (b) b.disabled = !selectedDataUrl; });
        }
    });
}

if (runAllBtn) {
    runAllBtn.addEventListener('click', async () => {
        if (!selectedDataUrl) return;
        runAllBtn.disabled = true;
        runBtn.disabled = true;
        try {
            await runAllAttacks();
        } catch (error) {
            statusText.textContent = error.message;
        } finally {
            runAllBtn.disabled = !selectedDataUrl;
            runBtn.disabled = !selectedDataUrl;
        }
    });
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
    runBtn.textContent = isLoading ? 'Processing...' : 'Run analysis';
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

    // Every metric goes through metric(): two decimals for display, where
    // the response carries four (lab-format.js).
    const { metric, metricPair } = window.revealxLabFormat;
    const m = data.metrics;
    document.getElementById('qualityResult').textContent = `${metric(m.reconstructed.psnr, 'dB')} / ${metric(m.reconstructed.ssim)}`;
    document.getElementById('qualityDetail').textContent = m.quality_safe_fallback
        ? `Enhancement lowered SSIM (${metricPair(m.enhanced_candidate.ssim, m.reconstructed.ssim).join(' vs ')}), so the plain reconstruction is reported instead.`
        : `Enhanced: ${metric(m.enhanced.psnr, 'dB')} / ${metric(m.enhanced.ssim)}`;
    document.getElementById('timeResult').textContent = `${Math.round(m.processing_ms)} ms`;

    document.getElementById('mseReconstructed').textContent = metric(m.reconstructed.mse);
    document.getElementById('psnrReconstructed').textContent = metric(m.reconstructed.psnr, 'dB');
    document.getElementById('ssimReconstructed').textContent = metric(m.reconstructed.ssim);
    const fallbackNote = m.quality_safe_fallback ? ' *' : '';
    document.getElementById('mseEnhanced').textContent = `${metric(m.enhanced.mse)}${fallbackNote}`;
    document.getElementById('psnrEnhanced').textContent = `${metric(m.enhanced.psnr, 'dB')}${fallbackNote}`;
    document.getElementById('ssimEnhanced').textContent = `${metric(m.enhanced.ssim)}${fallbackNote}`;

    // The Enhanced card shows the plain reconstruction when enhancement was
    // skipped, so its title and caption have to say so (lab-format.js).
    const enhanced = window.revealxLabFormat.enhancedCaption(data.metrics);
    document.getElementById('enhancedTitle').textContent = enhanced.title;
    document.getElementById('enhancedCaption').textContent = enhanced.caption;

    const fallbackEl = document.getElementById('fallbackNote');
    if (fallbackEl) {
        const [tried, plain] = metricPair(m.enhanced_candidate.ssim, m.reconstructed.ssim);
        fallbackEl.textContent = data.metrics.quality_safe_fallback
            ? '* Enhancement was skipped for this run: it scored SSIM ' +
              `${tried} against ${plain} for the raw reconstruction, ` +
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

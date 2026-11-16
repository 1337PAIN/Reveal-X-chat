/**
 * Reveal-X: how an image share looks in the chat.
 *
 * A share used to be a grey box of text ("Share 2 arrived end-to-end
 * encrypted...") above a green box listing every check line by line, and the
 * send dialog showed the picture letterboxed in white. This draws what a chat
 * app is expected to show for a picture:
 *   - a card with a sealed tile: grain standing in for the image, since
 *     neither share on its own is anything but noise, a lock, and a caption
 *     that says what it is;
 *   - once the recipient rebuilds the image, the picture itself in the tile,
 *     in this tab's memory only;
 *   - the tamper verdict as one line with a shield, and the evidence (HMAC,
 *     both ML verdicts, the end-to-end hash, the heatmap) one click below it,
 *     opened on its own when something is wrong.
 *
 * Everything is built with createElement and textContent, never markup, and
 * the grain is a stylesheet class, so the CSP needs nothing new.
 *
 * A classic script, like voice-ui.js, so tests/js/share-ui.test.mjs loads the
 * shipped file through harness.mjs.
 */

(function () {
    function icon(doc, name) {
        if (window.revealxIcons) return window.revealxIcons.icon(doc, name);
        const span = doc.createElement('span');
        span.setAttribute('aria-hidden', 'true');
        return span;
    }

    function pct(confidence) {
        return `${(confidence * 100).toFixed(1)}%`;
    }

    /**
     * What the tamper panel should say, from the server's analysis, the
     * in-browser one (if it ran) and the end-to-end hash (once Share 1 has
     * been fetched). Pure, so the rules are tested without a page.
     *
     * Any single failing check makes the verdict "tampered": the checks look
     * at different copies of the share, and a reassuring one does not cancel
     * an alarming one.
     */
    function verdictSummary(data, local, endToEnd) {
        const checks = [];
        checks.push({
            ok: Boolean(data.integrity.hmac_match),
            label: 'HMAC-SHA256',
            text: data.integrity.hmac_match
                ? 'match (bit-for-bit identical)'
                : 'MISMATCH (share was modified)',
        });
        // Once a local verdict exists, say which copy each row describes: the
        // server scored the file it holds, the browser the bytes that arrived.
        checks.push({
            ok: !data.ml.tampered,
            label: local ? 'Server ML (share as stored)' : 'ML',
            text: `${data.ml.label} - ${pct(data.ml.confidence)} confidence`,
        });
        if (local) {
            checks.push({
                ok: !local.tampered,
                label: 'In-browser ML (bytes you received)',
                text: `${local.label} - ${pct(local.confidence)} confidence `
                    + `(${local.timing_ms.total} ms, ONNX/wasm)`,
            });
            // The two engines are the same forest and agree to ~1e-7 on the
            // same input, so a split verdict means they saw different bytes.
            if (Boolean(local.tampered) !== Boolean(data.ml.tampered)) {
                checks.push({
                    ok: false,
                    label: 'Disagreement',
                    text: 'The server and your browser scored different data. '
                        + 'The share you received is not the one the server verified. '
                        + 'Treat it as tampered.',
                });
            }
        }
        // The only check that does not rely on the server at all.
        if (endToEnd) {
            checks.push({
                ok: Boolean(endToEnd.matches),
                label: 'End-to-end SHA-256',
                text: endToEnd.matches
                    ? "match (the sender's Share 1, unchanged)"
                    : 'MISMATCH (not the Share 1 the sender made)',
            });
        }

        const tampered = Boolean(data.integrity.exact_tamper_detected)
            || checks.some(check => !check.ok);
        const source = local || data.ml;
        return {
            tampered,
            title: tampered ? 'Tampering detected in Share 1' : 'Share 1 verified intact',
            timing: `${data.ml.model_name} - checked in ${data.processing_ms} ms`,
            passed: checks.filter(check => check.ok).length,
            checks,
            heatmap: source.heatmap_data_url || null,
            heatmapLocal: Boolean(local && local.heatmap_data_url),
            explanation: source.explanation || [],
        };
    }

    /** Fill a tamper panel element from verdictSummary's answer. */
    function drawVerdict(doc, panel, summary) {
        panel.className = `tamper-panel ${summary.tampered ? 'danger' : 'success'}`;
        panel.textContent = '';

        const head = doc.createElement('div');
        head.className = 'tamper-verdict';
        head.append(icon(doc, summary.tampered ? 'shield-alert' : 'shield-check'));
        const title = doc.createElement('span');
        title.className = 'tamper-title';
        title.textContent = summary.title;
        head.append(title);
        panel.append(head);

        const details = doc.createElement('details');
        details.className = 'tamper-details';
        // Good news needs no reading; bad news shows its evidence at once.
        if (summary.tampered) details.setAttribute('open', '');
        const label = doc.createElement('summary');
        label.textContent = `How it was checked - ${summary.passed} of ${summary.checks.length} passed`;
        details.append(label);

        const list = doc.createElement('ul');
        list.className = 'tamper-rows';
        summary.checks.forEach((check) => {
            const row = doc.createElement('li');
            row.className = `tamper-row ${check.ok ? 'ok' : 'bad'}`;
            const name = doc.createElement('span');
            name.className = 'tamper-row-label';
            name.textContent = `${check.label}: `;
            const text = doc.createElement('span');
            text.textContent = check.text;
            row.append(name, text);
            list.append(row);
        });
        details.append(list);

        const timing = doc.createElement('p');
        timing.className = 'tamper-timing';
        timing.textContent = summary.timing;
        details.append(timing);

        if (summary.heatmap) {
            const caption = doc.createElement('p');
            caption.className = 'tamper-heatmap-caption';
            caption.textContent = summary.heatmapLocal
                ? 'Suspicious-region heatmap and reasoning (computed in your browser)'
                : 'Suspicious-region heatmap and reasoning';
            const heatmap = doc.createElement('img');
            heatmap.className = 'tamper-heatmap';
            heatmap.src = summary.heatmap;
            heatmap.alt = 'Tamper heatmap: bright regions are statistically suspicious';
            const reasons = doc.createElement('ul');
            reasons.className = 'tamper-explanation';
            summary.explanation.forEach((line) => {
                const item = doc.createElement('li');
                item.textContent = line;
                reasons.append(item);
            });
            details.append(caption, heatmap, reasons);
        }
        panel.append(details);
    }

    /**
     * The sealed tile at the top of a share bubble.
     * @returns {{root, tile, caption, detail}}
     */
    function buildShareCard(doc, { sent } = {}) {
        const root = doc.createElement('div');
        root.className = 'share-card';

        const tile = doc.createElement('div');
        tile.className = 'share-tile';
        const grain = doc.createElement('span');
        grain.className = 'share-grain';
        grain.setAttribute('aria-hidden', 'true');
        const badge = doc.createElement('span');
        badge.className = 'share-lock';
        badge.setAttribute('aria-hidden', 'true');
        badge.append(icon(doc, 'lock'));
        tile.append(grain, badge);

        const label = doc.createElement('div');
        label.className = 'share-label';
        const caption = doc.createElement('span');
        caption.className = 'share-caption';
        caption.textContent = sent ? 'Image sent as two shares' : 'Image share';
        // What can be done with it, in words: render.js fills this
        // (.share-status) with who can rebuild it, or why it cannot be.
        const detail = doc.createElement('span');
        detail.className = 'share-detail share-status';
        label.append(caption, detail);

        root.append(tile, label);
        return { root, tile, caption, detail };
    }

    /**
     * Put the rebuilt picture in the tile. Memory only: after a reload the
     * share is spent and the tile is sealed again.
     */
    function showImage(doc, card, src) {
        const img = doc.createElement('img');
        img.className = 'share-image';
        img.src = src;
        img.alt = 'The rebuilt image';
        card.tile.textContent = '';
        card.tile.append(img);
        card.root.classList.add('revealed');
        card.caption.textContent = 'Image rebuilt';
        card.detail.textContent = 'In this tab only - gone when you reload';
    }

    /** "18 KB", "1.2 MB": bytes for people. */
    function formatBytes(bytes) {
        if (!Number.isFinite(bytes) || bytes < 0) return '';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    /** Decoded size of a base64 data: URL, without decoding it. */
    function dataUrlBytes(dataUrl) {
        const comma = typeof dataUrl === 'string' ? dataUrl.indexOf(',') : -1;
        if (comma < 0) return 0;
        const body = dataUrl.slice(comma + 1);
        const padding = body.endsWith('==') ? 2 : body.endsWith('=') ? 1 : 0;
        return Math.max(0, Math.floor((body.length * 3) / 4) - padding);
    }

    /** The line under the send preview: "photo.jpg - 1200 x 900 - 182 KB". */
    function previewMeta({ name, width, height, bytes }) {
        const parts = [];
        if (name) parts.push(name);
        if (width && height) parts.push(`${width} × ${height}`);
        const size = formatBytes(bytes);
        if (size) parts.push(size);
        return parts.join(' · ');
    }

    window.revealxShareUI = {
        verdictSummary,
        drawVerdict,
        buildShareCard,
        showImage,
        formatBytes,
        dataUrlBytes,
        previewMeta,
    };
})();

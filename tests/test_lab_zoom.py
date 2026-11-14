"""/lab's result images can be opened large.

The shares are noise at pixel scale, shown 220px tall in a card; the
difference between a clean and a tampered Share 1, or what the XOR left
behind, cannot be seen at that size. Each result image is now a button
(disabled until there is an image) that opens it in a native <dialog>:
shown modal, so focus moves in and Escape closes it, drawn with crisp
pixels, and focus goes back to the image it was opened from.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'css' / 'rx_lab.css').read_text(encoding='utf-8')


@pytest.mark.parametrize('image_id', [
    'imgOriginal', 'imgShare1Clean', 'imgShare1Received', 'imgShare2',
    'imgReconstructed', 'imgEnhanced',
])
def test_each_result_image_is_a_zoom_button(image_id):
    button = re.search(r'<button type="button" class="zoom-btn"[^>]*>\s*<img id="' + image_id + r'"', LAB)
    assert button, image_id
    tag = re.search(r'<button type="button" class="zoom-btn"[^>]*>', LAB[button.start():]).group(0)
    assert 'disabled' in tag and 'aria-label="Enlarge ' in tag


def test_the_zoom_view_is_a_labelled_modal_dialog():
    dialog = re.search(r'<dialog id="zoomDialog"[^>]*>', LAB).group(0)
    assert 'aria-labelledby="zoomTitle"' in dialog
    body = LAB[LAB.index('<dialog id="zoomDialog"'):LAB.index('</dialog>')]
    assert 'id="zoomTitle"' in body and 'id="zoomImage"' in body
    assert re.search(r'<button[^>]*id="zoomClose"[^>]*aria-label="Close enlarged image"', body)


def test_it_opens_modal_and_gives_focus_back():
    assert 'zoomDialog.showModal()' in LAB_JS
    assert "zoomDialog.addEventListener('close'" in LAB_JS
    assert 'zoomOpener.focus()' in LAB_JS
    # Buttons come alive with their image.
    assert 'zoom-btn' in LAB_JS and '.disabled = false' in LAB_JS


def test_shares_are_drawn_with_crisp_pixels():
    assert re.search(r'\.zoom-dialog img\s*\{[^}]*image-rendering:\s*pixelated', CSS)


def test_it_is_actually_enlarged():
    # Sized by its content the dialog shrank to the image's natural 240px.
    dialog = re.search(r'\.zoom-dialog\s*\{([^}]*)\}', CSS).group(1)
    assert re.search(r'(?<!max-)width:\s*min\(96vw', dialog)
    image = re.search(r'\.zoom-dialog img\s*\{([^}]*)\}', CSS).group(1)
    assert 'width: 100%' in image and 'object-fit: contain' in image

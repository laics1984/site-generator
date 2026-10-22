"""Measured technical quality of a photograph: sharpness and exposure.

Two physical measurements, from bytes a pixel pass already holds, combined into
one 0..1 number. It ranks photos — the sharp, well-exposed one first — and never
rejects one: a phone photo of the real classroom beats a stock image, and a
ranking can only reorder what the gates already let through. It makes no claim
about composition, subject or appeal, which pixels alone cannot measure.

**Sharpness** is the variance of a 4-neighbour Laplacian on a 512px greyscale
copy. Measured on 24 real photographs: originals 4–1650, the same photos
Gaussian-blurred by 2px at 512 never above 35, and mostly under 15. Variance
also depends on content (a smooth sky, bokeh), so the score is a log ramp from
`_SHARP_FLOOR` (nothing usable) to `_SHARP_FULL` (crisp), not a cut.

**Exposure** penalises a mean luminance outside `_EXPOSURE_BAND` and the share
of clipped pixels (≥250 or ≤5) beyond `_CLIP_ALLOWANCE`. The same photos at a
quarter brightness measured mean 0.01–0.24 with up to 78% crushed shadows.

`depicts_nothing` is a different question, and the one place an image IS
rejected: not "how good is this photo" but "is there a picture here at all".
A PDF printed from a browser (Chrome/Skia, the usual way a designed brief is
made) carries every CSS box-shadow as a blurred grey mask image and every
hatched "photo placeholder" box as a pattern tile — Fowable's 29-page brief
held eight such images and not one photograph, and each became a section's
"photo". Two physical classes, measured against 37 stock photos chosen to be
as featureless as photos get (clear sky, fog, calm sea, plain walls, snow):

    placeholder hatch       grey std 3.3      (nearest scene: clear sky, 9.5)
    box-shadow blur         Laplacian 1.1-2.4, colour variety 0.03-0.05
    smoothest photo         Laplacian 5.4 (calm sea), colour variety 0.54

So a near-uniform frame is nothing, and a frame with neither edges nor colour
variety is nothing; a smooth photo always has the noise and gradients that
give it variety, which is why variety must agree with the edge test.
"""

from __future__ import annotations

import math
from io import BytesIO

_EDGE = 512
_SHARP_FLOOR = 10.0
_SHARP_FULL = 300.0
_EXPOSURE_BAND = (0.18, 0.82)
_CLIP_ALLOWANCE = 0.10
_SHARPNESS_WEIGHT = 0.65
# depicts_nothing: a frame this uniform shows no scene; a frame with no edges
# (Laplacian variance under the sharpness floor) AND this little colour variety
# (distinct colours per pixel on a _VARIETY_EDGE thumbnail) is a blur mask.
_BLANK_MAX_STD = 6.0
_BLANK_MAX_VARIETY = 0.1
_VARIETY_EDGE = 96


def _greyscale(raw: bytes):
    import numpy as np
    from PIL import Image

    with Image.open(BytesIO(raw)) as image:
        grey = image.convert("L")
        grey.thumbnail((_EDGE, _EDGE))
        return np.asarray(grey, dtype=np.float32)


def _laplacian_variance(pixels) -> float:
    if pixels.shape[0] < 3 or pixels.shape[1] < 3:
        return 0.0
    laplacian = (
        pixels[:-2, 1:-1] + pixels[2:, 1:-1] + pixels[1:-1, :-2] + pixels[1:-1, 2:]
        - 4 * pixels[1:-1, 1:-1]
    )
    return float(laplacian.var())


def sharpness_score(pixels) -> float:
    variance = _laplacian_variance(pixels)
    if variance <= _SHARP_FLOOR:
        return 0.0
    ramp = math.log(variance / _SHARP_FLOOR) / math.log(_SHARP_FULL / _SHARP_FLOOR)
    return min(1.0, ramp)


def exposure_score(pixels) -> float:
    mean = float(pixels.mean()) / 255
    low, high = _EXPOSURE_BAND
    off_band = max(low - mean, mean - high, 0.0) / low
    clipped = float((pixels >= 250).mean() + (pixels <= 5).mean())
    over_clip = max(0.0, clipped - _CLIP_ALLOWANCE)
    return max(0.0, 1.0 - off_band - over_clip)


def photo_quality(raw: bytes) -> float | None:
    """0..1, or None when the bytes can't be decoded (unmeasured, not bad)."""
    try:
        pixels = _greyscale(raw)
    except Exception:
        return None
    score = (
        _SHARPNESS_WEIGHT * sharpness_score(pixels)
        + (1 - _SHARPNESS_WEIGHT) * exposure_score(pixels)
    )
    return round(score, 3)


def depicts_nothing(raw: bytes) -> bool:
    """True when the bytes hold no picture — a blank, a placeholder pattern or a
    shadow mask. Undecodable bytes are not judged (False)."""
    import numpy as np
    from PIL import Image

    try:
        pixels = _greyscale(raw)
        with Image.open(BytesIO(raw)) as image:
            colour = image.convert("RGB")
            colour.thumbnail((_VARIETY_EDGE, _VARIETY_EDGE))
            rgb = np.asarray(colour).reshape(-1, 3)
    except Exception:
        return False
    if float(pixels.std()) < _BLANK_MAX_STD:
        return True
    variety = len(np.unique(rgb, axis=0)) / max(1, len(rgb))
    return _laplacian_variance(pixels) <= _SHARP_FLOOR and variety < _BLANK_MAX_VARIETY

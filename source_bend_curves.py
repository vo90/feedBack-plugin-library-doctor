"""Source-specific bend conversion, independent of archive and audio readers."""
from collections.abc import Iterable
import math


def normalize_sng_bend_curve(
    note_time: float, sustain: float, points: Iterable[tuple[float, float]],
) -> tuple[list[dict[str, float]], set[str]]:
    """Convert absolute SNG timestamps to a curve inside the sounding window.

    SNG points are never interpreted as relative timestamps. Pre-onset and
    overrun segments are linearly sampled at the window boundaries, without
    shifting the interior points, extending the note, or inventing a release.
    An entirely pre-onset curve holds its last authored value from onset.
    Return adjustment categories so callers can report exceptional source data.
    """
    if not math.isfinite(note_time) or not math.isfinite(sustain) or sustain < 0:
        raise ValueError("Bend onset and sustain must be finite; sustain must be nonnegative")
    relative = []
    for absolute_time, value in points:
        if not math.isfinite(absolute_time) or not math.isfinite(value):
            raise ValueError("SNG bend points must be finite")
        point = (absolute_time - note_time, value)
        if relative and point[0] < relative[-1][0]:
            raise ValueError("SNG bend points must be in chronological order")
        if not relative or point != relative[-1]:
            relative.append(point)
    if not relative:
        return [], set()
    if len(relative) == 1 and relative[0][0] > 0 and relative[0][1] > 0:
        # One later SNG target describes a bend up to that authored point.
        # Explicit onset zero supplies interpolation support; it is not a
        # recovered source event. No release or extra target is invented.
        relative.insert(0, (0.0, 0.0))

    def sample(time: float) -> float:
        if time < relative[0][0]:
            return relative[0][1]
        previous = relative[0]
        for point in relative[1:]:
            if point[0] > time:
                if previous[0] == time:
                    return previous[1]
                ratio = (time - previous[0]) / (point[0] - previous[0])
                return previous[1] + ratio * (point[1] - previous[1])
            previous = point
        return relative[-1][1]

    adjustments = set()
    clipped = [(t, v) for t, v in relative if 0 <= t <= sustain]
    if relative[0][0] < 0:
        adjustments.add("entirely-pre-onset" if relative[-1][0] < 0 else "pre-onset")
        if not clipped or clipped[0][0] > 0:
            clipped.insert(0, (0.0, sample(0.0)))
    if relative[-1][0] > sustain:
        adjustments.add("past-sustain")
        if not clipped or clipped[-1][0] < sustain:
            clipped.append((sustain, sample(sustain)))
    result = []
    for time, value in clipped:
        point = {"t": round(time, 6), "v": round(value, 6)}
        if not result or point != result[-1]:
            result.append(point)
    return result, adjustments

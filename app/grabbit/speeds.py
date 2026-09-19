"""The recent history of transfer speed, kept for drawing.

Both front-ends graph the same thing from the same numbers, so the shape of
that history lives here rather than in either interface.
"""

from collections import deque

# One sample a second, so this is the last two minutes. Enough to see a
# download settle down or a torrent find its peers, and small enough to redraw
# sixty times a second without thinking about it.
DEFAULT_SAMPLES = 120


class SpeedHistory:
    """A fixed-length window of download and upload speeds, in bytes/second."""

    def __init__(self, samples: int = DEFAULT_SAMPLES):
        self.samples = max(8, int(samples))
        self.down = deque([0] * self.samples, maxlen=self.samples)
        self.up = deque([0] * self.samples, maxlen=self.samples)

    def push(self, down: int, up: int = 0) -> None:
        self.down.append(max(0, int(down or 0)))
        self.up.append(max(0, int(up or 0)))

    def clear(self) -> None:
        self.down.extend([0] * self.samples)
        self.up.extend([0] * self.samples)

    @property
    def latest(self) -> tuple:
        return self.down[-1], self.up[-1]

    @property
    def peak(self) -> int:
        return max(max(self.down), max(self.up))

    def scale(self) -> int:
        """The value the top of the graph should mean.

        Rounded up to something a person recognises, so the shape stops
        jittering every time the peak moves by a few bytes, and never zero -
        an idle graph still needs a height to be flat at.
        """
        peak = self.peak
        if peak <= 0:
            return 64 * 1024
        step = 64 * 1024
        while step < peak:
            step *= 2
        return step

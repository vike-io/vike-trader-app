"""Replay state machine — drives the bar-by-bar visual replay.

Qt-free and fully unit-tested. A QTimer in the UI calls :meth:`tick` on an interval;
the widgets read :attr:`index` / :attr:`playing` to render the current frame.
"""


class Replay:
    """Tracks the current bar index over ``n_bars`` and play/pause state."""

    def __init__(self, n_bars: int) -> None:
        self.n_bars = max(0, n_bars)
        self.index = 0
        self.playing = False

    @property
    def last_index(self) -> int:
        return max(0, self.n_bars - 1)

    @property
    def at_end(self) -> bool:
        return self.index >= self.last_index

    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def step(self) -> None:
        self.index = min(self.index + 1, self.last_index)

    def step_back(self) -> None:
        self.index = max(self.index - 1, 0)

    def seek(self, i: int) -> None:
        self.index = max(0, min(i, self.last_index))

    def tick(self) -> None:
        """One timer tick: advance if playing; auto-pause at the end."""
        if not self.playing:
            return
        self.step()
        if self.at_end:
            self.playing = False

"""Audio-lane errors. The worker error codes are mapped in B7."""


class MediaUnreadable(Exception):
    """The WAV is missing, not a WAV, not 16 kHz / 16-bit / mono, or has no frames."""


class DeadlineExceeded(Exception):
    """The job deadline (absolute UTC epoch milliseconds) has passed."""


class RecognizerError(Exception):
    """A torch or CUDA error during recognition. The original error is chained."""

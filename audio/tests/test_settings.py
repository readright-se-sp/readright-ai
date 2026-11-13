import pytest
from pydantic import ValidationError

from audio.config import AudioSettings


def test_threshold_defaults_load():
    s = AudioSettings()
    assert 0 <= s.T_FLAG < s.T_OVERRULE


@pytest.mark.parametrize(("f", "o"), [(0.5, 0.4), (0.4, 0.4), (-0.1, 0.4)])
def test_bad_thresholds_raise(f, o):
    with pytest.raises(ValidationError):
        AudioSettings(T_FLAG=f, T_OVERRULE=o)

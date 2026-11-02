import pytest
from pydantic import ValidationError

from audio.settings import AudioSettings


def test_threshold_defaults_load():
    s = AudioSettings()
    assert 0 <= s.t_flag < s.t_overrule


@pytest.mark.parametrize(("f", "o"), [(0.5, 0.4), (0.4, 0.4), (-0.1, 0.4)])
def test_bad_thresholds_raise(f, o):
    with pytest.raises(ValidationError):
        AudioSettings(t_flag=f, t_overrule=o)

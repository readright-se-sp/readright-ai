from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EXPRESSION_GROUP_NAMES = frozenset(
    {"brow_raise", "brow_down", "smile", "frown", "squint", "eye_wide", "sneer"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAI_VIDEO_", env_file=".env", extra="ignore")

    log_level: str = "INFO"
    allowed_media_dir: Path | None = None
    max_concurrent_jobs: int = Field(default=2, ge=1)
    stats_window: int = Field(default=20, ge=1)
    disconnect_poll_seconds: float = Field(default=0.5, gt=0)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    probe_timeout_seconds: float = Field(default=10, gt=0)
    sample_fps: float = Field(default=5.0, gt=0)
    frame_width: int = Field(default=640, ge=64)
    max_frames: int = Field(default=900, ge=1)
    model_dir: Path = Path("models")
    num_hands: int = Field(default=2, ge=1, le=4)
    use_pose: bool = False
    expression_min_face_coverage: float = Field(default=0.5, ge=0, le=1)
    expression_min_face_frames: int = Field(default=20, ge=1)
    expression_smooth_frames: int = Field(default=3, ge=1)
    expression_min_range: float = Field(default=0.12, ge=0, le=1)
    expression_group_min_range: dict[str, float] = {}
    pointing_min_hand_score: float = Field(default=0.5, ge=0, le=1)
    pointing_extended_max_bend_deg: float = Field(default=35, ge=0, le=180)
    pointing_curled_min_bend_deg: float = Field(default=60, ge=0, le=180)
    pointing_merge_gap_ms: float = Field(default=400, gt=0)
    pointing_min_episode_ms: float = Field(default=600, gt=0)
    pointing_min_total_ms: float = Field(default=2000, gt=0)
    pointing_min_total_fraction: float = Field(default=0.05, ge=0, le=1)
    pointing_use_sweeps: bool = True
    sweep_min_ms: float = Field(default=1000, gt=0)
    sweep_min_dx: float = Field(default=0.10, gt=0, le=1)
    sweep_return_max_ms: float = Field(default=1000, gt=0)
    sweep_min_count: int = Field(default=2, ge=1)
    pointing_pose_min_visibility: float = Field(default=0.5, ge=0, le=1)
    pointing_pose_bottom_band: float = Field(default=0.3, ge=0, le=1)

    @field_validator("expression_smooth_frames")
    @classmethod
    def _smooth_frames_odd(cls, v: int) -> int:
        if v % 2 == 0:
            raise ValueError("must be odd")
        return v

    @field_validator("expression_group_min_range")
    @classmethod
    def _group_ranges_valid(cls, v: dict[str, float]) -> dict[str, float]:
        unknown = set(v) - EXPRESSION_GROUP_NAMES
        if unknown:
            raise ValueError(f"unknown expression groups: {sorted(unknown)}")
        if any(not 0 <= x <= 1 for x in v.values()):
            raise ValueError("values must be between 0 and 1")
        return v

    @model_validator(mode="after")
    def _smooth_window_fits(self) -> "Settings":
        # A window wider than the shortest accepted series flattens every recording.
        if self.expression_smooth_frames > self.expression_min_face_frames:
            raise ValueError("expression_smooth_frames must not exceed expression_min_face_frames")
        return self

"""Cap source video at 1080p and 60 fps before the rest of the pipeline.

1080p means the frame fits in a 1920×1080 box in either orientation, so a
landscape 1920×1080 and a portrait 1080×1920 both pass. Anything larger is
scaled down with the aspect ratio preserved. Frame rates above 60 (60 and
59.94 stay) are dropped to 60. Audio is copied when the container allows it.

The working file is replaced in place. Transcript sidecars, storage cache
keys, and download cleanup all keep the same path. A fresh download of a
stored original is capped again the next time it is prepared.
"""

from __future__ import annotations

import fcntl
import json
import logging
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_LONG_EDGE = 1920
MAX_SHORT_EDGE = 1080
MAX_OUTPUT_FPS = 60
# 60/1 and 60000/1001 (59.94) are already within the cap.
FPS_EPSILON = 0.01


@dataclass(frozen=True)
class VideoGeometry:
    width: int
    height: int
    fps: float | None
    rotation: int

    @property
    def display_width(self) -> int:
        if abs(self.rotation) % 180 == 90:
            return self.height
        return self.width

    @property
    def display_height(self) -> int:
        if abs(self.rotation) % 180 == 90:
            return self.width
        return self.height


def parse_frame_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            rate = float(numerator) / denominator_value
        else:
            rate = float(value)
    except ValueError:
        return None
    if rate <= 0 or rate > 1000:
        return None
    return rate


def fit_within_1080p(width: int, height: int) -> tuple[int, int]:
    """Even dimensions that fit in 1920×1080, or the inputs when they already do."""
    if width <= 0 or height <= 0:
        return width, height
    long_edge = max(width, height)
    short_edge = min(width, height)
    scale = min(1.0, MAX_LONG_EDGE / long_edge, MAX_SHORT_EDGE / short_edge)
    if scale >= 1.0:
        return width, height

    def even_within(value: float, cap: int) -> int:
        rounded = int(round(value))
        if rounded % 2:
            rounded -= 1
        while rounded > cap:
            rounded -= 2
        return max(2, rounded)

    long_cap = MAX_LONG_EDGE
    short_cap = MAX_SHORT_EDGE
    if width >= height:
        return even_within(width * scale, long_cap), even_within(height * scale, short_cap)
    return even_within(width * scale, short_cap), even_within(height * scale, long_cap)


def exceeds_output_limits(width: int, height: int, fps: float | None) -> bool:
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge > MAX_LONG_EDGE or short_edge > MAX_SHORT_EDGE:
        return True
    return fps is not None and fps > MAX_OUTPUT_FPS + FPS_EPSILON


def _rotation_degrees(stream: dict) -> int:
    tags = stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(round(float(raw)))
        except (TypeError, ValueError):
            pass
    for side in stream.get("side_data_list") or []:
        if not isinstance(side, dict) or "rotation" not in side:
            continue
        try:
            return int(round(float(side["rotation"])))
        except (TypeError, ValueError):
            continue
    return 0


def probe_video_geometry(path: Path) -> VideoGeometry | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height,avg_frame_rate,r_frame_rate:stream_tags=rotate:stream_side_data=rotation",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        streams = json.loads(getattr(result, "stdout", "") or "{}").get("streams") or []
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return None
    if not streams:
        return None
    stream = streams[0]
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    fps = parse_frame_rate(stream.get("avg_frame_rate")) or parse_frame_rate(
        stream.get("r_frame_rate")
    )
    return VideoGeometry(width, height, fps, _rotation_degrees(stream))


def source_within_output_limits(path: Path) -> bool:
    """True when the file is already at most 1080p/60, or when it cannot be probed.

    An unreadable file keeps the caller's existing failure path instead of
    being treated as an oversized source.
    """
    geometry = probe_video_geometry(path)
    if geometry is None:
        return True
    return not exceeds_output_limits(
        geometry.display_width, geometry.display_height, geometry.fps
    )


def _has_audio(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "audio" in result.stdout


def build_normalization_command(
    source: Path,
    destination: Path,
    *,
    scale: tuple[int, int] | None,
    cap_frame_rate: bool,
    has_audio: bool,
    copy_audio: bool,
) -> list[str]:
    """ffmpeg command that writes a capped H.264 file. `scale` is the fitted size."""
    filters: list[str] = []
    if scale is not None:
        width, height = scale
        filters.append(f"scale={width}:{height}:flags=lanczos,setsar=1")
    if cap_frame_rate:
        filters.append(f"fps={MAX_OUTPUT_FPS}")
    command = ["ffmpeg", "-y", "-i", str(source), "-map", "0:v:0"]
    if has_audio:
        command += ["-map", "0:a?"]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio and copy_audio:
        command += ["-c:a", "copy"]
    elif has_audio:
        command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        command += ["-an"]
    command += ["-movflags", "+faststart", str(destination)]
    return command


def _run_ffmpeg(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Source normalization failed to run: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning("Source normalization ffmpeg failed: %s", result.stderr[-500:])
        return False
    return True


def _normalize_locked(path: Path) -> Path:
    geometry = probe_video_geometry(path)
    if geometry is None:
        logger.warning("Could not read video geometry for %s; leaving it unchanged", path.name)
        return path
    display_width = geometry.display_width
    display_height = geometry.display_height
    if not exceeds_output_limits(display_width, display_height, geometry.fps):
        return path

    fitted = fit_within_1080p(display_width, display_height)
    needs_scale = fitted != (display_width, display_height)
    needs_fps = geometry.fps is not None and geometry.fps > MAX_OUTPUT_FPS + FPS_EPSILON
    has_audio = _has_audio(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.capped.mp4")
    logger.info(
        "Normalizing source %s from %sx%s%s to %sx%s%s",
        path.name,
        display_width,
        display_height,
        f" at {geometry.fps:.2f} fps" if geometry.fps else "",
        fitted[0],
        fitted[1],
        f" at {MAX_OUTPUT_FPS} fps" if needs_fps else "",
    )
    try:
        wrote = _run_ffmpeg(build_normalization_command(
            path, temporary,
            scale=fitted if needs_scale else None,
            cap_frame_rate=needs_fps,
            has_audio=has_audio,
            copy_audio=True,
        ))
        if not wrote and has_audio:
            temporary.unlink(missing_ok=True)
            wrote = _run_ffmpeg(build_normalization_command(
                path, temporary,
                scale=fitted if needs_scale else None,
                cap_frame_rate=needs_fps,
                has_audio=True,
                copy_audio=False,
            ))
        if not wrote or not temporary.exists() or temporary.stat().st_size <= 0:
            raise RuntimeError(
                "Could not prepare this video for processing. "
                "Sources above 1080p or 60 fps are scaled down automatically, "
                "and that step failed."
            )
        temporary.replace(path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def normalize_source_media(path: Path) -> Path:
    """Scale a local working file down to at most 1080p and 60 fps.

    Files that are already within the cap, or that cannot be probed, are
    returned unchanged. A failed transcode of an oversized file raises so the
    pipeline does not continue with a source above the cap.
    """
    path = Path(path)
    if not path.is_file():
        return path
    lock_path = path.with_name(f".{path.name}.normalize.lock")
    with lock_path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            return _normalize_locked(path)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            lock_path.unlink(missing_ok=True)

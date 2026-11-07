"""Turn a video file into the handful of JPEG frames the embedding path eats.

MOVED HERE from backend/src/modelling/get_frames.py, which no longer exists - this is
the only copy. The code is the same pipeline;
two things about it had to change once it became part of a service that runs
unattended, and both are recorded here because they were real defects:

1. FFMPEG WAS INVOKED THROUGH A SHELL. The old call built one f-string and ran
   `subprocess.run(command, shell=True)`. Every path in that string was
   interpolated, so a file called `a; rm -rf /tmp/x.mp4` executed the second
   command. The sidecar accepts uploads from the network, so "the caller controls
   the path" is now literally true: the argv list below is the fix, and it is not
   optional.
2. NOBODY CHECKED THE RESULT. `shell=True` without `check=True` meant a missing
   ffmpeg, an unreadable container or a corrupt file produced an empty directory
   and a confusing failure several frames later. FrameExtractionError names the
   step that failed and carries the tail of ffmpeg's stderr.

The extraction parameters (10 fps, 224x224, numbered JPEGs) are unchanged: they
are what the Cosmos tokenizer and UNet3D were trained against, and changing them
would silently change every embedding this service produces.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import settings

logger = logging.getLogger("ml.frames")

# 10 fps is the pipeline's own sampling rate. It is a model parameter, not a
# performance knob: the tokenizer sees the frames selected from this stream (see
# select_frames), so a different rate shifts what "the middle of the clip" means.
FRAMES_PER_SECOND = 10

# ffmpeg's numbered-output pattern. Kept as a constant because the glob that
# reads the frames back has to stay in step with it.
FRAME_PATTERN = "%03d.jpg"

# How much of ffmpeg's stderr to quote in the error. It is usually one line, but
# the interesting line ("Invalid data found when processing input") is often the
# last one, so the tail is what gets kept.
STDERR_TAIL_CHARS = 400

# Upper bound on one ffmpeg run. A 40-minute clip at 10 fps decodes in seconds,
# so this only ever fires on a hung process (a network share that stopped
# answering, a corrupt container ffmpeg is spinning on). Without it one bad upload
# occupies the single worker forever and every later request queues behind it.
FRAME_EXTRACTION_TIMEOUT_SECONDS = 300


class FrameExtractionError(RuntimeError):
    """ffmpeg could not produce frames from this file.

    A RuntimeError subclass rather than a bare Exception so engine.py can catch it
    precisely and answer 500 inference_failed: the request was well formed and the
    dependency is installed, so this is the pipeline failing on the input.
    """


def frames_output_dir(video_path: Path, output_dir: Path) -> Path:
    """The directory this one video's frames live in.

    Keyed by the file stem, as the pipeline always did: the caller passes one
    video at a time, so collisions are impossible within a request, and a batch
    run over a directory keeps one subdirectory per clip.
    """
    return Path(output_dir) / Path(video_path).stem


def list_frames(frames_dir: Path) -> List[Path]:
    """The extracted frames, in numeric order.

    sorted() on the name is correct only because ffmpeg zero-pads to three digits
    (%03d). Lexicographic order equals numeric order up to 999 frames, which is
    100 seconds at 10 fps - the pipeline only ever needs 16.
    """
    directory = Path(frames_dir)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.suffix == ".jpg")


def select_frames(frames: Sequence[Path], num_frames: int = 16) -> List[Path]:
    """Pick and pad to EXACTLY num_frames frames.

    The tensor fed to the tokenizer has a fixed frame dimension, so the count has
    to be exact. Short clips repeat their last frame (the old behaviour, kept
    because a still tail is closer to the original clip than a black pad); a clip
    with no frames at all is an error, not something to pad.
    """
    available = list(frames)
    if not available:
        raise FrameExtractionError("ffmpeg produced no frames")
    if num_frames <= 0:
        raise FrameExtractionError("num_frames must be positive, got " + str(num_frames))
    if len(available) >= num_frames:
        return available[:num_frames]
    padded = available + [available[-1]] * (num_frames - len(available))
    logger.warning(
        "clip is shorter than %d frames (%d extracted); repeating the last frame",
        num_frames,
        len(available),
    )
    return padded


def extract_frames_ffmpeg(
    video_path,
    output_dir,
    num_frames: int = 16,
    frame_size: Tuple[int, int] = (224, 224),
    binary: Optional[str] = None,
    force: bool = False,
) -> Path:
    """Extract frames from video_path into output_dir and return the frame dir.

    Returns the directory rather than a list so the caller can hand it to
    select_frames (and, in the batch CLI, keep going after one bad file).

    force=True re-extracts even when frames are already present. The service always
    passes force=True: its scratch directory is fresh per request, and reusing
    "whatever is on disk" is exactly how the old code ended up trusting a
    half-written directory.
    """
    video = Path(video_path)
    target = frames_output_dir(video, Path(output_dir))

    if not force and len(list_frames(target)) >= num_frames:
        return target

    target.mkdir(parents=True, exist_ok=True)

    command = [
        binary or settings.ffmpeg_binary(),
        # -nostdin: ffmpeg would otherwise consume the container's stdin, and a
        # sidecar has none - it reads from the same fd as uvicorn.
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vf",
        "fps=" + str(FRAMES_PER_SECOND) + ",scale=" + str(frame_size[0]) + ":" + str(frame_size[1]),
        "-vsync",
        "vfr",
        str(target / FRAME_PATTERN),
    ]
    try:
        completed = subprocess.run(
            command,
            # NO shell: the argv list above is the whole point (defect 1).
            check=False,
            capture_output=True,
            text=True,
            timeout=FRAME_EXTRACTION_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise FrameExtractionError(
            "ffmpeg not found (" + str(command[0]) + "): set FFMPEG_BINARY or install it"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractionError(
            "ffmpeg timed out after " + str(FRAME_EXTRACTION_TIMEOUT_SECONDS) + "s on "
            + video.name
        ) from exc

    if completed.returncode != 0 or not list_frames(target):
        tail = (completed.stderr or "").strip()[-STDERR_TAIL_CHARS:]
        raise FrameExtractionError(
            "ffmpeg failed on " + video.name + " (exit " + str(completed.returncode)
            + "): " + (tail or "no diagnostic output")
        )

    return target


def process_videos(input_dir, output_dir) -> int:
    """Batch helper for maintainers: extract frames for every .mp4 in a folder.

    Kept because rebuilding a frame corpus offline is still a real task, and
    `python frames.py <in> <out>` on the CLI drives this. It returns the number of
    failures instead of raising, so one unreadable file does not discard a long run.
    """
    source = Path(input_dir)
    failures = 0
    for video in sorted(source.glob("*.mp4")):
        try:
            frames = extract_frames_ffmpeg(video, Path(output_dir), force=True)
            logger.info("%s -> %d frames", video.name, len(list_frames(frames)))
        except FrameExtractionError as exc:
            failures += 1
            logger.error("%s", exc)
    return failures


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract 16 frames per video with ffmpeg (offline batch tool)."
    )
    parser.add_argument("input_dir", type=str, help="directory of .mp4 files")
    parser.add_argument("output_dir", type=str, help="where the frames go")
    args = parser.parse_args(argv)
    failures = process_videos(args.input_dir, args.output_dir)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

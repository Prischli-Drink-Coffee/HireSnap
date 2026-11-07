"""Video/audio to transcript with wav2vec2.

LIVES IN ml/service/ BECAUSE IT IS THE SERVICE. Moved here from
backend/src/modelling/video_to_text.py, which no longer exists - this is the only copy.
What changed in the move:

1. EVERY HEAVY IMPORT IS INSIDE THE FUNCTION THAT NEEDS IT. The old module
   imported torch, torchaudio, moviepy and transformers at module scope, so
   importing it cost several seconds and any one of them being absent (moviepy
   needs ffmpeg, torchaudio is missing from many wheels) made the whole module
   unimportable. The HTTP layer imports this module at start-up and must not pay
   that, and /health must still answer on a host without the ASR stack - that is
   the "degraded" state the contract allows.
2. NO tqdm AND NO pandas. The batch CLI is offline tooling; a progress bar in a
   container nobody watches is noise, and the transcript table is written with the
   csv module (the old pandas path also read the table back, which crashed when the
   file existed but was empty).
3. save_table=False IS HONEST. The old signature required path_to_table_speech even
   when nothing was saved; the HTTP path passed None and relied on the pandas
   branch never being reached.

The decoding arithmetic is untouched: mono-mean, resample to 16 kHz, argmax over
the CTC logits, batch_decode. Changing any of it changes transcripts that the
embedding model was trained to expect.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
from typing import Any, Optional, Sequence, Tuple

logger = logging.getLogger("ml.video_to_text")

# wav2vec2 was trained on 16 kHz mono; anything else is resampled first. Named so
# the number appears once instead of in three conditionals.
TARGET_SAMPLE_RATE = 16000


def video2audio(path_to_video: str, audio_folder: str) -> str:
    """Extract the audio track of a video into a .wav under audio_folder.

    moviepy is imported HERE so that this module, and the HTTP app that imports it,
    stay cheap, and so a machine without moviepy fails with a message about
    transcription rather than failing to import the service at all.
    """
    from moviepy import editor as mpy

    # The moviepy logger writes progress bars to stderr; a container has no
    # terminal and those lines interleave with the service log.
    mpy.config.change_settings({"logger": None})

    os.makedirs(audio_folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path_to_video))[0]
    audio_path = os.path.join(audio_folder, stem + ".wav")
    clip = mpy.VideoFileClip(path_to_video)
    try:
        if clip.audio is None:
            # A silent video is a caller error worth naming: without this the
            # failure is "NoneType has no attribute write_audiofile".
            raise ValueError("no audio track in " + os.path.basename(path_to_video))
        clip.audio.write_audiofile(
            audio_path, codec="pcm_s16le", verbose=False, logger=None
        )
    finally:
        # ffmpeg subprocesses hold the file open; leaking the reader keeps the temp
        # directory undeletable on Windows.
        clip.close()
    return audio_path


def load_models(model_id: str, device: str = "cpu") -> Tuple[Any, Any]:
    """(processor, model) for the CTC model. The caller caches the pair."""
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    # transformers logs every layer it loads at INFO; in a service that loads this
    # once per container that noise hides the one line that matters.
    logging.getLogger("transformers").setLevel(logging.ERROR)
    processor = Wav2Vec2Processor.from_pretrained(model_id)
    model = Wav2Vec2ForCTC.from_pretrained(model_id).to(device)
    return processor, model


def audio2text(
    path_to_audio: str,
    path_to_table_speech: Optional[str] = None,
    processor: Any = None,
    model: Any = None,
    device: str = "cpu",
    save_table: bool = True,
) -> str:
    """Transcribe one .wav. Optionally append a row to a CSV transcript table."""
    import torch
    import torchaudio

    speech_array, sampling_rate = torchaudio.load(path_to_audio)
    if speech_array.shape[0] > 1:
        # mean over channels; keepdim so the shape stays (1, samples)
        speech_array = torch.mean(speech_array, dim=0, keepdim=True)
    if sampling_rate != TARGET_SAMPLE_RATE:
        resampler = torchaudio.transforms.Resample(
            orig_freq=sampling_rate, new_freq=TARGET_SAMPLE_RATE
        )
        speech_array = resampler(speech_array)

    inputs = processor(
        speech_array.squeeze().numpy(),
        sampling_rate=TARGET_SAMPLE_RATE,
        return_tensors="pt",
        padding=True,
    )
    input_values = inputs.input_values.to(device)
    mask = inputs.attention_mask
    attention_mask = mask.to(device) if mask is not None else None

    with torch.no_grad():
        logits = model(input_values, attention_mask=attention_mask).logits
    predicted_ids = torch.argmax(logits, dim=-1)
    sentence = processor.batch_decode(predicted_ids)[0]

    if save_table and path_to_table_speech:
        _append_table(path_to_table_speech, path_to_audio, sentence)
    return sentence


def _append_table(
    path_to_table_speech: str, path_to_audio: str, sentence: str
) -> None:
    """Append (candidate, speech) to the transcript CSV, creating it if needed.

    stdlib csv instead of pandas: this runs once per video in an offline job, and
    pandas is not a dependency of the sidecar image (docker/requirements.txt). A
    file that exists but is empty counts as absent, which the old pandas branch did
    not handle.
    """
    stem = os.path.splitext(os.path.basename(path_to_audio))[0]
    has_rows = (
        os.path.exists(path_to_table_speech)
        and os.path.getsize(path_to_table_speech) > 0
    )
    with open(path_to_table_speech, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not has_rows:
            writer.writerow(["candidate", "speech"])
        writer.writerow([stem, sentence])


def _video_paths(
    video_file_path: Optional[str], video_folder_path: Optional[str]
):
    """Exactly one of file/folder, as a list of paths."""
    if bool(video_file_path) == bool(video_folder_path):
        raise SystemExit(
            "specify exactly one of --video_file_path or --video_folder_path"
        )
    if video_file_path:
        return [video_file_path]
    folder = video_folder_path or ""
    return [
        os.path.join(folder, name)
        for name in sorted(os.listdir(folder))
        if name.lower().endswith(".mp4")
    ]


def transcribe_file(
    path_to_video: str,
    processor: Any,
    model: Any,
    device: str = "cpu",
    audio_folder: Optional[str] = None,
    keep_audio: bool = False,
    path_to_table_speech: Optional[str] = None,
) -> str:
    """Video in, transcript out, for one file, including audio cleanup.

    The HTTP engine calls this so that the "make a temp dir, extract, decode,
    remove the wav" sequence exists once instead of in the route, the CLI and any
    future caller. audio_folder is caller-owned: when it is None a temporary
    directory is created AND removed here.
    """
    import shutil
    import tempfile

    owned = audio_folder is None
    folder = audio_folder or tempfile.mkdtemp(prefix="ml-asr-")
    try:
        audio_path = video2audio(path_to_video, folder)
        return audio2text(
            path_to_audio=audio_path,
            path_to_table_speech=path_to_table_speech,
            processor=processor,
            model=model,
            device=device,
            save_table=bool(path_to_table_speech),
        )
    finally:
        if owned or not keep_audio:
            # Only ever remove what this function created or was handed as scratch.
            # The old CLI finished with shutil.rmtree(audio_folder) on a
            # user-supplied directory, which deleted real data.
            shutil.rmtree(folder, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Offline CLI: transcribe one file or a folder of videos."""
    parser = argparse.ArgumentParser(
        description="Convert video to audio and transcribe speech to text."
    )
    parser.add_argument("--video_file_path", type=str, default=None)
    parser.add_argument("--video_folder_path", type=str, default=None)
    parser.add_argument(
        "--table_path", type=str, default="data/candidates_table.csv"
    )
    parser.add_argument("--audio_folder", type=str, default=None)
    parser.add_argument("--model_id", type=str, default=None)
    args = parser.parse_args(argv)

    import settings

    device = settings.device() or "cpu"
    processor, model = load_models(args.model_id or settings.asr_model_id(), device)
    for path_to_video in _video_paths(args.video_file_path, args.video_folder_path):
        logger.info("transcribing %s", os.path.basename(path_to_video))
        sentence = transcribe_file(
            path_to_video,
            processor,
            model,
            device=device,
            audio_folder=args.audio_folder,
            path_to_table_speech=args.table_path,
        )
        print(sentence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The whole candidate embedding: one video in, one 384-d vector out.

LIVES IN ml/service/ BECAUSE IT IS THE SERVICE. Moved here from
backend/src/modelling/user_embedding.py, which no longer exists - this is the only copy.
The seven steps are unchanged - frames,
Cosmos latents, UNet3D, audio, wav2vec2 transcript, sentence embedding, average -
because changing any of them changes what the trained weights mean. What changed is
the four things that made the old module unusable from a service:

1. IT RAN ITSELF ON IMPORT. The last three statements of the old file called
   get_user_embedding() on a hardcoded data/files/*.mp4 path at module level, with no
   __main__ guard, so importing it started a full GPU pipeline. That is why the old
   sidecar needed an AST rewrite of the source before it could import the module at
   all; the call is simply gone now.
2. IT DELETED DIRECTORIES IT DID NOT OWN. The end of the function rmtree-d both
   temp folders, including ones the caller passed in. Two concurrent requests
   sharing a scratch path destroyed each other, and an operator who pointed
   frames_temp_folder at a real directory lost it. Now a directory is created per
   call (tempfile.mkdtemp) when the caller does not supply one, and only a directory
   this function created is removed.
3. THE COSMOS CHECKPOINT WAS A RELATIVE STRING. "pretrained_ckpts/..." was resolved
   against the working directory, so the mount only worked from one CWD. It comes
   from settings.cosmos_encoder_path() via video_embedding.load_tokenizer().
4. AUTOCAST WAS UNCONDITIONAL. torch.cuda.amp.autocast() raises on a CPU host, so
   the whole path was unusable there (docs/KNOWN_GAPS.md#7). video_embedding.
   autocast_context() is now the single decision point and CPU works.

The text and audio models are BUILT PER CALL here on purpose: this is the one-shot
offline path, and engine.embed_video() is the only caller in the service. It does
not cache because the pipeline reloads them anyway; the long-lived embeddings the
service serves come from engine.embed_text(), which does cache.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Sequence

import settings
from frames import extract_frames_ffmpeg, list_frames, select_frames
from video_to_text import audio2text, load_models, video2audio

logger = logging.getLogger("ml.user_embedding")

# The tokenizer and UNet3D are 224x224 models fed 16 frames; the same constant
# video_embedding.py calls FRAME_SIZE.
FRAME_SIZE = (224, 224)


def _video_backend():
    """video_embedding, imported on FIRST USE.

    WHY NOT AT MODULE SCOPE: video_embedding.py defines UNet3D with torch.nn, so it
    has to import torch at module level. This module is imported by engine.py, which
    app.py imports at start-up, and docs/SERVICE_CONTRACTS.md 1.1 wants /health to
    answer without paying for CUDA initialisation. One deferred import keeps the
    model definitions in the module that owns them and the HTTP layer torch-free.
    """
    import video_embedding

    return video_embedding


def _frames_tensor(video_path: Path, frames_dir: Path, device, num_frames: int):
    """Extract frames and stack them into the (1, C, T, H, W) tensor the tokenizer eats."""
    # torchvision is imported here, not at module scope: the service imports this
    # module at start-up and must survive a host where torchvision is absent.
    import torch
    from PIL import Image
    from torchvision import transforms

    extract_frames_ffmpeg(
        video_path=video_path,
        output_dir=frames_dir,
        num_frames=num_frames,
        frame_size=FRAME_SIZE,
        # force: the scratch directory is fresh per call, and trusting whatever is
        # already on disk is how the old code ended up reading a half-written run.
        force=True,
    )
    frames_path = Path(frames_dir) / video_path.stem
    selected = select_frames(list_frames(frames_path), num_frames=num_frames)

    transform = transforms.Compose(
        [transforms.Resize(FRAME_SIZE), transforms.ToTensor()]
    )
    frames = [transform(Image.open(path).convert("RGB")) for path in selected]
    return torch.stack(frames).unsqueeze(0).permute(0, 2, 1, 3, 4).to(device).to(
        torch.float32
    )


def _video_vector(video_tensor, unet_weights_path: Path, device):
    """Cosmos latents -> UNet3D encoder -> one 384-d numpy vector.

    Both halves of the model are loaded per call: the encode happens on the same
    card and the weights file is a local read, so caching them would only matter for
    a stream of videos, which this path does not serve.
    """
    import torch

    video_embedding = _video_backend()
    encoder = video_embedding.load_tokenizer(device)
    with torch.no_grad():
        # THE fix for KNOWN_GAPS#7: on CPU this is a no-op context manager, on CUDA
        # it is fp16 autocast. The old unconditional call raised on every CPU host.
        with video_embedding.autocast_context(device):
            latent = encoder.encode(video_tensor)[0]
    del encoder

    unet = video_embedding.UNet3D(
        in_channels=video_embedding.LATENT_CHANNELS,
        out_channels=1,
        base_filters=32,
        latent_dim=video_embedding.LATENT_DIM,
    ).to(device)
    unet.load_state_dict(torch.load(str(unet_weights_path), map_location=device))
    unet.eval()

    with torch.no_grad():
        with video_embedding.autocast_context(device):
            _, video_vector = unet(latent.to(device).to(torch.float32))
    unet.cpu()
    return video_vector.float().cpu().numpy().squeeze(0)


def _audio_transcript(video_path: Path, audio_dir: Path, device, asr_model_id: str) -> str:
    """Extract the audio track and decode it with wav2vec2."""
    audio_path = video2audio(path_to_video=str(video_path), audio_folder=str(audio_dir))
    processor, model = load_models(model_id=asr_model_id, device=device)
    try:
        return audio2text(
            path_to_audio=audio_path,
            path_to_table_speech=None,
            processor=processor,
            model=model,
            device=device,
            save_table=False,
        )
    finally:
        # The wav is scratch; the model pair is left to GC because reloading it per
        # video in a batch is the caller batch loop concern, not this function.
        model.cpu()


def _text_vector(sentence: str, device, text_model_id: str):
    """Sentence embedding for the transcript, as a numpy vector."""
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(text_model_id).to(device)
    tensor = encoder.encode(sentence, device=str(device), convert_to_tensor=True)
    vector = tensor.detach().float().cpu().numpy()
    del encoder
    return vector


def _unit(vector, name: str):
    """L2-normalise one half, refusing an all-zero vector.

    A zero vector has no direction, so (text + video) / 2 with one of them zero is
    silently half the other; downstream cosine distance then ranks it as unrelated
    to everything, including itself.
    """
    import numpy as np

    array = np.asarray(vector, dtype="float64").reshape(-1)
    norm = float(np.linalg.norm(array))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("the " + name + " encoder produced a zero vector")
    return array / norm


def get_user_embedding(
    video_path,
    unet_weights_path=None,
    output_embedding_path=None,
    frames_temp_folder=None,
    audio_temp_folder=None,
    num_frames: Optional[int] = None,
    device=None,
    asr_model_id: Optional[str] = None,
    text_model_id: Optional[str] = None,
):
    """Full candidate embedding for one video file.

    Every optional path resolves from settings: unet_weights_path from
    settings.unet_weights_path(), asr/text model ids from settings, the device from
    settings.device(). The two scratch folders default to directories this call
    creates and removes; when the caller supplies one, THIS FUNCTION DOES NOT DELETE
    IT (defect 2 above).
    """
    import numpy as np

    video = Path(video_path)
    if not video.is_file():
        raise FileNotFoundError("video not found: " + str(video))

    video_embedding = _video_backend()
    frames_per_call = video_embedding.NUM_FRAMES if num_frames is None else int(num_frames)
    weights = Path(unet_weights_path) if unet_weights_path else settings.unet_weights_path()
    asr_id = asr_model_id or settings.asr_model_id()
    text_id = text_model_id or settings.text_model_id()
    torch_device = video_embedding.resolve_device(
        settings.device() if device is None else str(device)
    )

    owned_frames = frames_temp_folder is None
    owned_audio = audio_temp_folder is None
    frames_dir = Path(frames_temp_folder or tempfile.mkdtemp(prefix="ml-frames-"))
    audio_dir = Path(audio_temp_folder or tempfile.mkdtemp(prefix="ml-audio-"))
    try:
        # 1. frames -> tensor
        video_tensor = _frames_tensor(video, frames_dir, torch_device, frames_per_call)
        # 2. + 3. frames -> Cosmos latents -> UNet3D -> 384-d video vector
        video_embedding = _video_vector(video_tensor, weights, torch_device)
        del video_tensor
        # 4. + 5. audio -> wav2vec2 -> transcript
        sentence = _audio_transcript(video, audio_dir, torch_device, asr_id)
        # 6. transcript -> sentence embedding
        text_embedding = _text_vector(sentence, torch_device, text_id)
    finally:
        # Only what this call created. The old code removed caller-owned folders.
        if owned_frames:
            shutil.rmtree(frames_dir, ignore_errors=True)
        if owned_audio:
            shutil.rmtree(audio_dir, ignore_errors=True)

    # 7. the candidate representation: mean of two unit vectors, so the result is
    # still unit length and comparable with cosine distance.
    embedding = (_unit(text_embedding, "text") + _unit(video_embedding, "video")) / 2

    if output_embedding_path is not None:
        np.save(output_embedding_path, embedding)
        logger.info("saved embedding to %s", output_embedding_path)

    return embedding


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Offline CLI: embed one video and optionally save the .npy.

    Replaces the unguarded module-level demo call. Nothing runs on import.
    """
    parser = argparse.ArgumentParser(
        description="Compute one candidate embedding from a video file."
    )
    parser.add_argument("video_path", type=str, help="path to the .mp4 file")
    parser.add_argument("--weights", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--num_frames", type=int, default=None)
    parser.add_argument("--device", type=str, default=None)
    args = parser.parse_args(argv)

    embedding = get_user_embedding(
        video_path=args.video_path,
        unet_weights_path=args.weights,
        output_embedding_path=args.output,
        num_frames=args.num_frames,
        device=args.device,
    )
    print("dim=" + str(len(embedding)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

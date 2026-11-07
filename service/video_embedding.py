"""The video half of the candidate embedding: Cosmos latents -> UNet3D -> vector.

LIVES IN ml/service/ BECAUSE IT IS THE SERVICE, and this is the only copy: the old one
lived in backend/src/modelling/get_video_embedding.py, was reached through a sys.path
shim and could only be exercised by the API container. Three defects were the direct
result of nobody owning it:

1. THE COSMOS CHECKPOINT PATH WAS A STRING LITERAL. `pretrained_ckpts/...` was
   resolved against the process working directory, so the bind mount worked from
   one CWD and silently produced FileNotFoundError from another. It is
   configuration now (settings.cosmos_encoder_path()).
2. AUTOCAST WAS UNCONDITIONAL. `torch.cuda.amp.autocast()` on a CPU host raises
   before a single frame is read, which made the CPU build of this pipeline
   unusable (docs/KNOWN_GAPS.md#7). autocast_context() below is the only place
   that decides.
3. THE FRAME COUNT WAS ASSUMED. `frame_files[-1]` on an empty list raised
   IndexError three call frames deep, with a message about indexing rather than
   about the video that produced no frames.

UNet3D is reproduced EXACTLY: its attribute names are the keys of the trained
state dict in Unet3D.pth, so renaming a module here makes every weight file ever
trained unloadable. The architecture is an artifact, not a design choice.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from PIL import Image
from torch.utils.data import DataLoader, Dataset

import settings

logger = logging.getLogger("ml.video_embedding")

# Must match frames.py and user_embedding.py: the tokenizer and the UNet were
# trained on 224x224 stills, 16 of them. A mismatch surfaces as a shape error
# several seconds into an inference, not at load time.
FRAME_SIZE = (224, 224)
NUM_FRAMES = 16
# Cosmos-Tokenizer-CV4x8x8 emits 5 latent channels, and the UNet consumes them as
# input channels. Named once and reused so the two cannot drift apart.
LATENT_CHANNELS = 5
LATENT_DIM = 384


class VideoFrameDataset(Dataset):
    """One item per video directory: a (C, T, H, W) float tensor of frames."""

    def __init__(self, data_dir, num_frames: int = NUM_FRAMES):
        self.data_dir = Path(data_dir)
        self.video_ids = sorted(
            entry.name for entry in self.data_dir.iterdir() if entry.is_dir()
        )
        # torchvision is imported HERE rather than at module scope: the HTTP layer
        # imports this module at start-up, and a torchvision that is missing or
        # ABI-incompatible must not stop /health from answering.
        from torchvision import transforms

        self.transform = transforms.Compose(
            [transforms.Resize(FRAME_SIZE), transforms.ToTensor()]
        )
        self.num_frames = num_frames

    def __len__(self):
        return len(self.video_ids)

    def __getitem__(self, idx):
        video_path = self.data_dir / self.video_ids[idx]
        frame_files = sorted(path.name for path in video_path.glob("*.jpg"))
        if not frame_files:
            # Defect 3: say which video produced no frames instead of raising
            # IndexError from a list subscript later on.
            raise ValueError("no .jpg frames under " + str(video_path))
        if len(frame_files) >= self.num_frames:
            selected = frame_files[: self.num_frames]
        else:
            # Repeat the last frame rather than pad with black: a still tail is
            # closer to the original clip than an artificial edge.
            selected = frame_files + [frame_files[-1]] * (
                self.num_frames - len(frame_files)
            )
        frames = [
            self.transform(Image.open(video_path / name).convert("RGB"))
            for name in selected
        ]
        return torch.stack(frames).permute(1, 0, 2, 3)


class EmbeddingDataset(Dataset):
    """Latents from the tokenizer, one tensor per clip."""

    def __init__(self, embeddings):
        self.embeddings = embeddings

    def __len__(self):
        return len(self.embeddings)

    def __getitem__(self, idx):
        return self.embeddings[idx]


class UNet3D(nn.Module):
    """3D autoencoder whose bottleneck is the candidate embedding (384 dims).

    The decoder half exists because the network was TRAINED as an autoencoder;
    inference only reads `latent` (user_embedding.py). Both halves must still be
    defined, because load_state_dict rejects a checkpoint with unexpected keys.
    """

    def __init__(
        self, in_channels, out_channels, base_filters=32, latent_dim=LATENT_DIM
    ):
        super().__init__()
        self.enc1 = self.conv_block(in_channels, base_filters)
        self.enc2 = self.conv_block(base_filters, base_filters * 2)
        self.enc3 = self.conv_block(base_filters * 2, base_filters * 4)
        self.adaptive_pool = nn.AdaptiveAvgPool3d(1)
        self.fc_latent = nn.Linear(base_filters * 4, latent_dim)
        self.upconv2 = nn.ConvTranspose3d(
            base_filters * 4, base_filters * 2, kernel_size=2, stride=2
        )
        self.dec2 = self.conv_block(base_filters * 4, base_filters * 2)
        self.upconv1 = nn.ConvTranspose3d(
            base_filters * 2, base_filters, kernel_size=2, stride=2
        )
        self.dec1 = self.conv_block(base_filters * 2, base_filters)
        self.out_conv = nn.Conv3d(base_filters, out_channels, kernel_size=1)

    def conv_block(self, in_channels, out_channels, kernel_size=3, padding=1):
        return nn.Sequential(
            nn.Conv3d(
                in_channels, out_channels, kernel_size=kernel_size, padding=padding
            ),
            nn.BatchNorm3d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv3d(
                out_channels, out_channels, kernel_size=kernel_size, padding=padding
            ),
            nn.BatchNorm3d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        # (B, T, C, H, W) -> (B, C, T, H, W): the 3D convolutions treat the frame
        # axis as depth, so the frame axis has to sit where depth sits.
        x = x.permute(0, 2, 1, 3, 4)
        enc1 = self.enc1(x)
        enc2 = self.enc2(F.max_pool3d(enc1, 2))
        enc3 = self.enc3(F.max_pool3d(enc2, 2))
        pooled = self.adaptive_pool(enc3).view(enc3.size(0), -1)
        latent = self.fc_latent(pooled)
        up2 = self.upconv2(enc3)
        dec2 = self.dec2(torch.cat([up2, enc2], dim=1))
        up1 = self.upconv1(dec2)
        dec1 = self.dec1(torch.cat([up1, enc1], dim=1))
        out = self.out_conv(dec1)
        return out.permute(0, 2, 1, 3, 4), latent


def autocast_context(device):
    """Autocast on CUDA, an inert context manager on CPU.

    ONE place that knows the rule. The old code called
    torch.cuda.amp.autocast() unconditionally from two modules, so a CPU host
    raised before reading a frame (docs/KNOWN_GAPS.md#7). Callers write
    `with autocast_context(device):` and stop caring which device they got.
    """
    name = str(device)
    if name.startswith("cuda") and torch.cuda.is_available():
        return torch.autocast(device_type="cuda", dtype=torch.float16)
    return contextlib.nullcontext()


def resolve_device(configured: str = "") -> torch.device:
    """cuda when a card is present and nothing else was asked for, else cpu.

    An explicit `cuda` on a CPU host is an ERROR, not a silent fall-back:
    quietly running a 16-frame 3D CNN on the CPU turns a misconfiguration into a
    request that takes minutes and looks like a hang.
    """
    choice = (configured or "").strip().lower()
    if choice == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if choice == "cuda":
        raise RuntimeError("DEVICE=cuda but this host has no CUDA device")
    return torch.device("cpu")


def load_tokenizer(device, checkpoint=None):
    """The Cosmos tokenizer encoder, from the configured checkpoint.

    Imported inside the function so that merely importing this module (which the
    HTTP layer does at start-up) does not build the jit machinery; the
    checkpoint path is configuration, never the process working directory.
    """
    from cosmos_tokenizer.video_lib import CausalVideoTokenizer

    path = Path(checkpoint) if checkpoint else settings.cosmos_encoder_path()
    if not path.is_file():
        raise FileNotFoundError(
            "Cosmos tokenizer checkpoint not found: "
            + str(path)
            + " (set PRETRAINED_CKPT_DIR to the directory holding "
            + settings.COSMOS_TOKENIZER_DIRNAME
            + ")"
        )
    return CausalVideoTokenizer(checkpoint_enc=str(path)).to(device)


def encode_videos(frame_dirs, device, encoder=None, num_frames: int = NUM_FRAMES):
    """Encode frame tensors into Cosmos latents, one tensor per clip.

    Split out of the old __main__ block so the offline tool and any future corpus
    build share one implementation, and so the autocast decision is made once.
    """
    tokenizer = encoder or load_tokenizer(device)
    latents = []
    with torch.no_grad():
        for frames in frame_dirs:
            tensor = frames.unsqueeze(0).to(device).to(torch.float32)
            with autocast_context(device):
                latents.append(tokenizer.encode(tensor)[0].cpu())
    return latents


def train_autoencoder(
    model, data_loader, num_epochs, learning_rate, device, weights_path
):
    """Offline training loop; not reachable from the HTTP layer.

    It lives beside the inference code because it is what produces Unet3D.pth:
    keeping the trainer and the consumer in one module is what keeps the
    architecture and the weights file in step.
    """
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)
    best_loss = float("inf")
    for epoch in range(num_epochs):
        model.train()
        running_loss = 0.0
        for inputs in data_loader:
            inputs = inputs.to(device).to(torch.float32)
            optimizer.zero_grad()
            with autocast_context(device):
                outputs, _ = model(inputs)
                loss = criterion(outputs, inputs)
            loss.backward()
            optimizer.step()
            running_loss += loss.item()
        epoch_loss = running_loss / max(1, len(data_loader))
        logger.info("epoch %d/%d loss %.4f", epoch + 1, num_epochs, epoch_loss)
        if epoch_loss < best_loss:
            best_loss = epoch_loss
            torch.save(model.state_dict(), weights_path)
            logger.info(
                "saved best weights (loss %.4f) to %s", best_loss, weights_path
            )


def eval_autoencoder(model, data_loader, ids, embeddings_path, device):
    """Offline: write one .npy embedding per id from an already-trained model."""
    model.eval()
    output_dir = Path(embeddings_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        for idx, inputs in enumerate(data_loader):
            inputs = inputs.to(device).to(torch.float32)
            with autocast_context(device):
                _, outputs = model(inputs)
            array = outputs.float().cpu().numpy()
            # Same normalisation as user_embedding.py: cosine distance downstream
            # only means something if the vectors are unit length.
            array = array / np.linalg.norm(array, axis=1, keepdims=True)
            np.save(output_dir / (str(ids[idx]) + ".npy"), array)
    logger.info("wrote embeddings to %s", output_dir)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Offline CLI for the batch tooling. Never called by the HTTP server."""
    parser = argparse.ArgumentParser(
        description="Train or evaluate the UNet3D video embedder."
    )
    parser.add_argument("--data_dir", type=str, required=True)
    parser.add_argument("--batch_size", type=int, default=4)
    parser.add_argument("--num_frames", type=int, default=NUM_FRAMES)
    parser.add_argument("--num_epochs", type=int, default=20)
    parser.add_argument("--learning_rate", type=float, default=1e-3)
    parser.add_argument("--weights_path", type=str, required=True)
    parser.add_argument("--embeddings_path", type=str, required=True)
    parser.add_argument(
        "--model_state", type=str, default="train", choices=["train", "inference"]
    )
    args = parser.parse_args(argv)

    dataset = VideoFrameDataset(args.data_dir, num_frames=args.num_frames)
    # No DataLoader over the dataset: frames are tokenized once into latents and the
    # training loop batches THOSE (embedding_loader below). A frame loader here would
    # decode every video twice for nothing.
    device = resolve_device(settings.device())

    encoder = load_tokenizer(device)
    latents = encode_videos(
        (dataset[index] for index in range(len(dataset))),
        device,
        encoder,
        args.num_frames,
    )
    del encoder
    embeddings = torch.cat(latents, dim=0)

    embedding_loader = DataLoader(
        EmbeddingDataset(embeddings),
        batch_size=args.batch_size,
        shuffle=(args.model_state == "train"),
    )

    # Shape comes from the data, but the channel count must match the tokenizer;
    # a silent mismatch would train a network that can never load the shipped
    # weights.
    model = UNet3D(
        in_channels=int(embeddings.shape[1]),
        out_channels=1,
        base_filters=32,
        latent_dim=int(embeddings.shape[2]),
    ).to(device)

    if args.model_state == "train":
        train_autoencoder(
            model,
            embedding_loader,
            num_epochs=args.num_epochs,
            learning_rate=args.learning_rate,
            device=device,
            weights_path=args.weights_path,
        )
    else:
        model.load_state_dict(torch.load(args.weights_path, map_location=device))
        eval_autoencoder(
            model=model,
            data_loader=embedding_loader,
            ids=dataset.video_ids,
            embeddings_path=args.embeddings_path,
            device=device,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

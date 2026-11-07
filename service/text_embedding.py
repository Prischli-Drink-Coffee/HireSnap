"""The text half of the embedding pipeline: sentences -> unit vectors.

LIVES IN ml/service/ BECAUSE IT IS THE SERVICE. Moved here from
backend/src/modelling/get_text_embedding.py, which no longer exists - this is the only
copy. What changed in the move:

1. NO tqdm, NO pandas. This module is imported by a container whose only job is
   inference; a progress bar writes to stderr of a process nobody watches, and
   pandas was used to read a two-column CSV. `csv` from the standard library does
   that without a 30 MB import.
2. NO MODULE-LEVEL MODEL IMPORT. sentence_transformers pulls torch; importing it
   at module scope means /health cannot answer until torch is importable, which is
   exactly the coupling the sidecar exists to keep out of the API container.
3. THE NORMALISATION IS A TORCH OP ON A TORCH TENSOR. The old line divided a
   CUDA tensor by a numpy norm, which works only because numpy dispatches on
   __array__ and moves the tensor to host mid-batch. F.normalize keeps it on the
   device and is what the cosine-distance consumer downstream assumes.

The batch CLI at the bottom is offline tooling (build a corpus of embeddings from
a CSV or pickle of transcripts). The HTTP path uses load_model() + encode_texts()
so that a long-running process builds the encoder once, not once per request.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import pickle
from typing import Optional, Sequence, Tuple

import numpy as np

import settings

logger = logging.getLogger("ml.text_embedding")


def get_text_pickle(path_to_pickle: str) -> Tuple[list, list]:
    """{candidate_id: transcript} -> (ids, texts), the .pkl input format."""
    with open(path_to_pickle, "rb") as handle:
        transcription = pickle.load(handle)
    return list(transcription.keys()), list(transcription.values())


def get_text_csv(path_to_csv: str, candidates: bool) -> Tuple[list, list]:
    """Two-column CSV -> (ids, texts).

    `candidates=True` means the first column is the id; otherwise the id is the
    1-based row number, which is what the candidate-ranking scripts expect.
    """
    with open(path_to_csv, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError("no rows in " + path_to_csv)
    if candidates:
        ids = [row[0] for row in rows]
    else:
        ids = [index + 1 for index in range(len(rows))]
    texts = [row[1] for row in rows]
    return ids, texts


def load_model(model_id: Optional[str] = None, device: str = "cpu"):
    """Build the sentence encoder. Imported lazily; see the module docstring."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_id or settings.text_model_id()).to(device)


def encode_texts(model, texts, batch_size: int = 8, device: str = "cpu") -> np.ndarray:
    """Encode a list of strings into an (n, dim) array of unit vectors.

    L2 normalisation is not optional: the pipeline compares candidates with
    cosine distance, and an un-normalised vector makes that a length comparison.
    """
    import torch
    import torch.nn.functional as F

    batches = []
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            encoded = model.encode(batch, device=device, convert_to_tensor=True)
            batches.append(F.normalize(encoded, p=2, dim=1).cpu().numpy())
    if not batches:
        # An empty corpus is a caller error, but np.vstack on an empty list raises
        # a message about shapes; say what actually happened.
        raise ValueError("no texts to encode")
    return np.vstack(batches)


def encode_text(
    path_to_texts: str,
    device: str = "cpu",
    batch_size: int = 8,
    candidates: bool = True,
    model_id: Optional[str] = None,
    output_folder: str = "data/train/",
):
    """Offline batch job: encode a CSV/pickle of transcripts to one .npy per id."""
    filename, ext = os.path.splitext(os.path.basename(path_to_texts))
    if ext == ".pkl":
        ids, texts = get_text_pickle(path_to_texts)
        # The pickle ids carry a file extension ("clip.mp4"); an embedding is named
        # after the clip, not the file it was decoded from.
        ids = [str(id_)[:-4] for id_ in ids]
    elif ext == ".csv":
        ids, texts = get_text_csv(path_to_texts, candidates=candidates)
    else:
        raise ValueError("path_to_texts should be .csv or .pkl format")

    model = load_model(model_id, device=device)
    matrix = encode_texts(model, texts, batch_size=batch_size, device=device)

    os.makedirs(output_folder, exist_ok=True)
    for index, identifier in enumerate(ids):
        np.save(os.path.join(output_folder, str(identifier) + ".npy"), matrix[index])
    logger.info("wrote %d embeddings to %s", len(ids), output_folder)
    return matrix


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Encode texts to embeddings and save one .npy per id."
    )
    parser.add_argument("--path_to_texts", type=str, required=True)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--model_id", type=str, default=None)
    parser.add_argument("--output_folder", type=str, default="data/train/")
    parser.add_argument(
        "--disable_candidates",
        action="store_false",
        help="Treat the CSV id column as absent and number rows from 1.",
    )
    args = parser.parse_args(argv)
    encode_text(
        path_to_texts=args.path_to_texts,
        device=settings.device() or "cpu",
        candidates=args.disable_candidates,
        batch_size=args.batch_size,
        model_id=args.model_id,
        output_folder=args.output_folder,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

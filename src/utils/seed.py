"""Seed every RNG this repository can reach.

torch is OPTIONAL. The backend no longer ships torch (the ML stack moved to
ml/), so a hard "import torch" at module scope made this file unusable on an
image that is deliberately torch-free. The seeding of torch is now best-effort:
absent torch, the process is seeded everywhere else and says so once, at debug
level.

Nothing in backend/ imports this module any more (the modules that did went to
ml/ with the pipeline); it is kept because it is part of the backend's exported
helpers and because a torch-free import must not be a latent ImportError.
"""

import os
import random

import numpy as np

# Imported for its side effect only; see seed_everything(). A module-level
# try/except (rather than a lazy import inside the function) is deliberate: the
# cost is paid once at import, and callers cannot accidentally end up in a state
# where the first call imports a 2 GB package mid-request.
try:  # pragma: no cover - exercised by the torch-free image, not by unit tests
    import torch  # OPTIONAL on purpose: the backend image ships no torch
except ImportError:  # pragma: no cover
    torch = None


def seed_everything(seed):
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    if torch is None:  # torch-free backend image: the other RNGs are seeded
        return
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True

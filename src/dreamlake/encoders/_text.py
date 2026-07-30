"""BGE sentence embeddings — the standard recipe: CLS pooling, then L2 norm.

The default checkpoint, ``BAAI/bge-small-en-v1.5`` (384-dim), is part of
the compatibility contract with @xenova/transformers — do not change it
without changing the browser side too. Loaded via plain ``transformers``
(AutoTokenizer/AutoModel); no sentence-transformers dependency.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

DEFAULT_TEXT_MODEL = "BAAI/bge-small-en-v1.5"


class TextEncoder:
    """Embeds text with BGE; vectors are float32, CLS-pooled, L2-normalized.

    Construction is free — the model lazy-loads (and downloads, on first
    ever use) at the first :meth:`encode` call. Inputs are batched
    internally so large calls don't exhaust memory.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_TEXT_MODEL,
        device: str | None = None,
        batch_size: int = 32,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self._device = device
        self._model = None
        self._tokenizer = None

    @property
    def device(self) -> str:
        if self._device is None:
            from ._device import auto_device

            self._device = auto_device()
        return self._device

    def _load(self):
        if self._model is None:
            from transformers import AutoModel, AutoTokenizer

            self._model = (
                AutoModel.from_pretrained(self.model_name).to(self.device).eval()
            )
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        return self._model, self._tokenizer

    def encode(self, text: str | Sequence[str]) -> np.ndarray:
        """Embed text -> ``(N, 384)`` float32, rows L2-normalized.

        A single string returns ``(384,)``; a sequence returns ``(N, 384)``.
        """
        single = isinstance(text, str)
        texts = [text] if single else list(text)
        model, tokenizer = self._load()
        if not texts:
            return np.empty((0, model.config.hidden_size), dtype=np.float32)

        import torch

        parts = []
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                inputs = tokenizer(
                    texts[i : i + self.batch_size],
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=512,
                ).to(self.device)
                hidden = model(**inputs).last_hidden_state
                cls = hidden[:, 0]  # CLS pooling — the standard BGE recipe
                cls = cls / cls.norm(dim=-1, keepdim=True)
                parts.append(cls.float().cpu().numpy())
        out = np.concatenate(parts, axis=0).astype(np.float32, copy=False)
        return out[0] if single else out

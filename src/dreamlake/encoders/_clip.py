"""CLIP image/text embeddings into one shared 512-dim space.

The default checkpoint, ``openai/clip-vit-base-patch32``, is part of the
compatibility contract with @xenova/transformers — do not change it without
changing the browser side too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Sequence

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from PIL.Image import Image

DEFAULT_CLIP_MODEL = "openai/clip-vit-base-patch32"


def _projected(out):
    """Unwrap ``get_image_features``/``get_text_features`` output.

    transformers 4.x returns the projected features as a bare tensor;
    5.x returns a ``BaseModelOutputWithPooling`` whose ``pooler_output``
    holds them.
    """
    return getattr(out, "pooler_output", out)


class ClipEncoder:
    """Embeds images and text with CLIP; vectors are float32, L2-normalized.

    Construction is free — the model lazy-loads (and downloads, on first
    ever use) at the first ``encode_*`` call. Inputs are batched internally
    so large calls don't exhaust memory.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_CLIP_MODEL,
        device: str | None = None,
        batch_size: int = 32,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self._device = device
        self._model = None
        self._processor = None

    @property
    def device(self) -> str:
        if self._device is None:
            from ._device import auto_device

            self._device = auto_device()
        return self._device

    def _load(self):
        if self._model is None:
            from transformers import CLIPModel, CLIPProcessor

            self._model = (
                CLIPModel.from_pretrained(self.model_name).to(self.device).eval()
            )
            self._processor = CLIPProcessor.from_pretrained(self.model_name)
        return self._model, self._processor

    def encode_images(self, images: "Image | Iterable[Image]") -> np.ndarray:
        """Embed PIL images -> ``(N, 512)`` float32, rows L2-normalized.

        Accepts a single image or any iterable of images; always returns a
        2-D array.
        """
        from PIL.Image import Image as PILImage

        if isinstance(images, PILImage):
            images = [images]
        images = list(images)
        model, processor = self._load()
        if not images:
            return np.empty((0, model.config.projection_dim), dtype=np.float32)

        import torch

        parts = []
        with torch.inference_mode():
            for i in range(0, len(images), self.batch_size):
                batch = images[i : i + self.batch_size]
                inputs = processor(images=batch, return_tensors="pt").to(self.device)
                feats = _projected(model.get_image_features(**inputs))
                feats = feats / feats.norm(dim=-1, keepdim=True)
                parts.append(feats.float().cpu().numpy())
        return np.concatenate(parts, axis=0).astype(np.float32, copy=False)

    def encode_text(self, text: str | Sequence[str]) -> np.ndarray:
        """Embed text into the same space as :meth:`encode_images`.

        A single string returns ``(512,)``; a sequence returns ``(N, 512)``.
        Float32, L2-normalized.
        """
        single = isinstance(text, str)
        texts = [text] if single else list(text)
        model, processor = self._load()
        if not texts:
            return np.empty((0, model.config.projection_dim), dtype=np.float32)

        import torch

        parts = []
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                inputs = processor(
                    text=texts[i : i + self.batch_size],
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                ).to(self.device)
                feats = _projected(model.get_text_features(**inputs))
                feats = feats / feats.norm(dim=-1, keepdim=True)
                parts.append(feats.float().cpu().numpy())
        out = np.concatenate(parts, axis=0).astype(np.float32, copy=False)
        return out[0] if single else out

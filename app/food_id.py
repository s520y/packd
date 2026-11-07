"""Identifies visible dishes with a local classifier trained on Food-101."""

import io
import os
from pathlib import Path

import certifi
from PIL import Image
import torch
from torch import nn
from torchvision.models import EfficientNet_V2_S_Weights, efficientnet_v2_s

os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # Lets Python download the base model.

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "food101_head.pt"
MIN_CONFIDENCE = 0.6  # Hints below this are dropped. Packaging usually lands here.
GRID_ROWS = 2
GRID_COLUMNS = 3


def load_backbone(device: str):
    """Pretrained image model that turns a photo into a feature vector."""
    weights = EfficientNet_V2_S_Weights.IMAGENET1K_V1
    model = efficientnet_v2_s(weights=weights)
    model.classifier = nn.Identity()
    return model.eval().to(device), weights.transforms()


class FoodIdentifier:
    def __init__(self):
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        saved = torch.load(MODEL_PATH, map_location="cpu")
        self.class_names = saved["class_names"]
        self.backbone, self.transform = load_backbone(self.device)
        state = saved["state_dict"]
        self.head = nn.Linear(state["weight"].shape[1], state["weight"].shape[0])
        self.head.load_state_dict(state)
        self.head.eval().to(self.device)

    @staticmethod
    def _crops(image: Image.Image) -> list[Image.Image]:
        """Overlapping crops, so a single dish can fill one crop."""
        width, height = image.size
        crop_w, crop_h = width // 2, height // 2 * 3 // 2
        crops = [image]
        for row in range(GRID_ROWS):
            for column in range(GRID_COLUMNS):
                left = (width - crop_w) * column // (GRID_COLUMNS - 1)
                top = (height - crop_h) * row // (GRID_ROWS - 1)
                crops.append(image.crop((left, top, left + crop_w, top + crop_h)))
        return crops

    @torch.no_grad()
    def identify(self, jpeg: bytes) -> dict[str, float]:
        """Returns confident dish names with the best score for each."""
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        batch = torch.stack([self.transform(crop) for crop in self._crops(image)]).to(self.device)
        scores = self.head(self.backbone(batch)).softmax(dim=1).cpu()
        confidence, indices = scores.max(dim=1)

        found: dict[str, float] = {}
        for score, index in zip(confidence.tolist(), indices.tolist()):
            if score >= MIN_CONFIDENCE:
                name = self.class_names[index].replace("_", " ")
                found[name] = max(found.get(name, 0.0), round(score, 2))
        return found

"""Trains the Food-101 classifier and reports accuracy on held-out images.

Uses a pretrained image model to turn each photo into features, then trains a
small classifier on those features. Takes minutes on Apple silicon, not hours.

Run from the tray-check folder:
  .venv/bin/python -m training.train_food101 --cache-dir /path/for/5gb/download
"""

import argparse
from pathlib import Path

from datasets import load_dataset
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, TensorDataset

from app.food_id import load_backbone

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "models" / "food101_head.pt"
EPOCHS = 30
BATCH_SIZE = 256


class FoodImages(Dataset):
    def __init__(self, rows, transform):
        self.rows = rows
        self.transform = transform

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        return self.transform(row["image"].convert("RGB")), row["label"]


@torch.no_grad()
def extract_features(rows, backbone, transform, device, name, cache_path):
    if cache_path.exists():
        print(f"{name}: using saved features", flush=True)
        return torch.load(cache_path)

    loader = DataLoader(
        FoodImages(rows, transform), batch_size=64, num_workers=8, persistent_workers=False
    )
    features, labels = [], []
    for step, (images, batch_labels) in enumerate(loader):
        features.append(backbone(images.to(device)).cpu())
        labels.append(batch_labels)
        if step % 100 == 0:
            print(f"{name}: {step * 64}/{len(rows)} images", flush=True)
    result = (torch.cat(features), torch.cat(labels))
    torch.save(result, cache_path)
    return result


def accuracy(head, features, labels, device, top_k=1):
    with torch.no_grad():
        scores = head(features.to(device))
        best = scores.topk(top_k).indices.cpu()
    return (best == labels.unsqueeze(1)).any(dim=1).float().mean().item()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    dataset = load_dataset("ethz/food101", cache_dir=str(args.cache_dir))
    class_names = dataset["train"].features["label"].names
    print(len(dataset["train"]), "training images,", len(dataset["validation"]), "test images", flush=True)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    backbone, transform = load_backbone(device)
    train_x, train_y = extract_features(
        dataset["train"], backbone, transform, device, "train", args.cache_dir / "train_features.pt"
    )
    test_x, test_y = extract_features(
        dataset["validation"], backbone, transform, device, "test", args.cache_dir / "test_features.pt"
    )

    head = nn.Linear(train_x.shape[1], len(class_names)).to(device)
    optimizer = torch.optim.AdamW(head.parameters(), lr=1e-3, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, EPOCHS)
    loss_function = nn.CrossEntropyLoss(label_smoothing=0.1)
    loader = DataLoader(TensorDataset(train_x, train_y), batch_size=BATCH_SIZE, shuffle=True)

    for epoch in range(EPOCHS):
        for features, labels in loader:
            optimizer.zero_grad()
            loss_function(head(features.to(device)), labels.to(device)).backward()
            optimizer.step()
        schedule.step()
        if epoch % 5 == 4:
            print(f"epoch {epoch + 1}: test top-1 {accuracy(head, test_x, test_y, device):.3f}", flush=True)

    top1 = accuracy(head, test_x, test_y, device)
    top5 = accuracy(head, test_x, test_y, device, top_k=5)
    print(f"Final test accuracy: top-1 {top1:.3f}, top-5 {top5:.3f}", flush=True)

    OUTPUT.parent.mkdir(exist_ok=True)
    torch.save(
        {"state_dict": head.cpu().state_dict(), "class_names": class_names, "top1": top1, "top5": top5},
        OUTPUT,
    )
    print("Saved", OUTPUT, flush=True)


if __name__ == "__main__":
    main()

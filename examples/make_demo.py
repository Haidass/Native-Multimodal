"""Create small synthetic fixtures, not real training data, under this project only."""
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageFilter


def main():
    root = Path(__file__).resolve().parents[1]/"runs"/"demo_input"
    if root.exists():
        raise SystemExit(f"Demo input already exists: {root}")
    root.mkdir(parents=True)
    rng = np.random.default_rng(42)
    coarse = Image.fromarray(rng.integers(20, 235, (32, 32, 3), dtype=np.uint8))
    image = coarse.resize((768, 512), Image.Resampling.BICUBIC)
    image.save(root/"landscape.png")
    shutil.copyfile(root/"landscape.png", root/"same_file.png")
    image.save(root/"same_pixels.bmp")
    image.save(root/"recompressed.jpg", quality=92)
    image.transpose(Image.Transpose.ROTATE_90).save(root/"portrait.png")
    image.filter(ImageFilter.GaussianBlur(6)).save(root/"blurred.png")
    raw = np.asarray(image).astype(np.float64)
    noisy = np.clip(raw + rng.normal(0, 15, raw.shape), 0, 255).astype(np.uint8)
    Image.fromarray(noisy).save(root/"noisy.png")
    image.resize((80, 60)).save(root/"small.png")
    Image.new("RGB", (512, 512), "white").save(root/"flat.png")
    Image.new("RGBA", (512, 512), (0, 255, 0, 0)).save(root/"transparent.png")
    Image.new("RGB", (32, 1024), "blue").save(root/"extreme_aspect.png")
    (root/"broken.jpg").write_bytes(b"invalid-image-fixture")
    for path in root.iterdir():
        if path.suffix.lower() in {".png", ".jpg", ".bmp"}:
            path.with_suffix(".txt").write_text("合成测试图片，用于验证清洗工具。", encoding="utf-8")
    print(root)


if __name__ == "__main__":
    main()

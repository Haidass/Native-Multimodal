"""Create six synthetic size cases and one corrupt fixture inside the project."""
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[1]/"runs"/"resolution_demo_input"
    if root.exists():
        raise SystemExit(f"Demo input already exists: {root}")
    root.mkdir(parents=True)
    for i, (width, height) in enumerate([(256, 256), (384, 256), (256, 384),
                                        (255, 384), (257, 384), (512, 512)]):
        image = Image.new("RGB", (width, height), (35+i*25, 60, 100))
        draw = ImageDraw.Draw(image)
        draw.rectangle((15, 15, width-15, height-15), outline="white", width=3)
        draw.text((25, 25), f"{width}x{height}", fill="white")
        path = root/f"{i:02d}_{width}x{height}.png"
        image.save(path)
        path.with_suffix(".txt").write_text(f"合成尺寸测试图：{width}×{height}。", encoding="utf-8")
    (root/"broken.jpg").write_bytes(b"corrupt-fixture")
    print(root)


if __name__ == "__main__":
    main()

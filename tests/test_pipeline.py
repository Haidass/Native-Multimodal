import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageFilter

from image_cleaner.config import Config, load_config
from image_cleaner.images import analyze_one, canonical_image, normalize_text
from image_cleaner.io import manifest_items, prepare
from image_cleaner.jobs import analyze, export_images, hash_bands, merge
from image_cleaner.metrics import quality_metrics


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1]/"runs"/"test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root/"input"
        self.images.mkdir()
        rng = np.random.default_rng(123)
        self.rgb = Image.fromarray(rng.integers(0, 256, (96, 128, 3), dtype=np.uint8))

    def save(self, name, image=None):
        path = self.images/name
        (image or self.rgb).save(path)
        return path

    def row(self, path, config=Config()):
        return analyze_one(({"seq": 0, "id": "sample-000000000000", "path": path.name}, str(path), config.to_dict()))

    def rows(self, directory, kind):
        return list(manifest_items(directory/f"{kind}.jsonl"))

    def test_pixel_duplicates_and_corrupt_files(self):
        self.save("one.png")
        self.save("two.bmp")
        (self.images/"broken.jpg").write_bytes(b"not a jpeg")
        manifest = self.root/"input.jsonl"
        prepare(self.images, manifest)
        analyze(manifest, self.root/"shards", Config())
        output = self.root/"curated"
        result = merge(self.root/"shards", output)
        self.assertEqual(result, {"input_count": 3, "kept_count": 1, "rejected_count": 0,
                                  "duplicate_count": 1, "error_count": 1})
        self.assertEqual(self.rows(output, "duplicate")[0]["duplicate_kind"], "exact_pixels")
        report = json.loads((output/"report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["before"]["distributions"]["width"]["count"], 2)
        self.assertEqual(report["after"]["distributions"]["width"]["count"], 1)
        self.assertTrue((output/"report.html").exists())

    def test_alpha_export_after_json_config_roundtrip(self):
        self.save("transparent.png", Image.new("RGBA", (96, 96), (10, 20, 30, 0)))
        config = Config(alpha_background=(12, 34, 56))
        restored = Config(**json.loads(json.dumps(config.to_dict())))
        self.assertEqual(restored.alpha_background, (12, 34, 56))
        manifest = self.root/"input.jsonl"
        prepare(self.images, manifest)
        analyze(manifest, self.root/"shards", config)
        merge(self.root/"shards", self.root/"curated")
        result = export_images(self.root/"curated", self.root/"exported")
        self.assertEqual(result, {"ok": 1})
        output = self.rows(self.root/"exported", "exported")[0]
        with Image.open(self.root/"exported"/output["path"]) as image:
            self.assertEqual(image.getpixel((0, 0)), (12, 34, 56))
        self.assertEqual(export_images(self.root/"curated", self.root/"exported", resume=True), {"ok": 1})

    def test_single_and_two_shards_are_identical_and_resume(self):
        self.save("a.png")
        self.save("b.bmp")
        self.save("c.png", Image.new("RGB", (60, 100), "blue"))
        self.save("d.png", Image.new("RGB", (60, 100), "red"))
        manifest = self.root/"input.jsonl"
        prepare(self.images, manifest)
        config = Config()
        analyze(manifest, self.root/"single", config, workers=2)
        analyze(manifest, self.root/"distributed", config, workers=2, rank=0, world_size=2)
        with self.assertRaisesRegex(ValueError, "Missing"):
            merge(self.root/"distributed", self.root/"incomplete")
        analyze(manifest, self.root/"distributed", config, workers=2, rank=1, world_size=2)
        result = analyze(manifest, self.root/"distributed", config, rank=1, world_size=2, resume=True)
        self.assertEqual(result["new_records"], 0)
        merge(self.root/"single", self.root/"single_out")
        merge(self.root/"distributed", self.root/"dist_out")
        for kind in ["kept", "duplicate", "rejected", "error"]:
            self.assertEqual(self.rows(self.root/"single_out", kind), self.rows(self.root/"dist_out", kind))
        self.assertEqual(len(self.rows(self.root/"dist_out", "kept")), 3)  # Different flat colors survive.

    def test_exif_and_alpha_normalization(self):
        path = self.images/"rotated.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (40, 20), "red").save(path, exif=exif)
        rgb, metadata = canonical_image(path, Config())
        self.assertEqual(rgb.size, (20, 40))
        self.assertTrue(metadata["exif_transposed"])
        alpha = self.save("alpha.png", Image.new("RGBA", (20, 20), (255, 0, 0, 0)))
        rgb, metadata = canonical_image(alpha, Config())
        self.assertEqual(rgb.getpixel((0, 0)), (255, 255, 255))
        self.assertTrue(metadata["alpha_flattened"])

    def test_noise_estimate_and_blur_are_separate(self):
        rng = np.random.default_rng(13)
        clean = np.full((256, 256), 128.0)
        noisy = np.clip(clean + rng.normal(0, 8, clean.shape), 0, 255).astype(np.uint8)
        metrics = quality_metrics(Image.fromarray(noisy).convert("RGB"))
        self.assertGreater(metrics["noise_sigma"], 4)
        self.assertLess(metrics["noise_sigma"], 12)
        checker = ((np.indices((256, 256)).sum(axis=0)//8) % 2 * 255).astype(np.uint8)
        sharp = Image.fromarray(checker).convert("RGB")
        blurred = sharp.filter(ImageFilter.GaussianBlur(3))
        self.assertGreater(quality_metrics(sharp)["blur_score"], quality_metrics(blurred)["blur_score"])
        flat = quality_metrics(Image.new("RGB", (128, 128), "white"))
        self.assertIsNone(flat["snr_proxy_db"])

    def test_filters_and_refilter_without_redecode(self):
        self.save("large.png")
        self.save("small.png", Image.new("RGB", (20, 30), "green"))
        manifest = self.root/"manifest.jsonl"
        prepare(self.images, manifest)
        analyze(manifest, self.root/"shards", Config())
        result = merge(self.root/"shards", self.root/"out", Config(min_short_edge=50))
        self.assertEqual(result["rejected_count"], 1)
        self.assertIn("min_short_edge", self.rows(self.root/"out", "rejected")[0]["reasons"])
        with self.assertRaisesRegex(ValueError, "analysis setting"):
            merge(self.root/"shards", self.root/"invalid", Config(metric_patch_size=128))

    def test_captions_preserved_and_image_scope_optional(self):
        path = self.save("a.png")
        input_manifest = self.root/"raw.jsonl"
        records = [{"path": str(path), "id": "original1", "text": "first", "license": "example"},
                   {"path": str(path), "id": "original2", "text": "second"},
                   {"path": str(path), "id": "original3", "text": "first"}]
        input_manifest.write_text("".join(json.dumps(r)+"\n" for r in records), encoding="utf-8")
        manifest = self.root/"manifest.jsonl"
        prepare(input_manifest, manifest)
        analyze(manifest, self.root/"shards", Config())
        result = merge(self.root/"shards", self.root/"out")
        self.assertEqual(result["kept_count"], 2)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(self.rows(self.root/"out", "kept")[0]["metadata"]["license"], "example")
        result = merge(self.root/"shards", self.root/"image_scope", Config(duplicate_scope="image"))
        self.assertEqual(result["kept_count"], 1)

    def test_export_resume_and_changed_source(self):
        path = self.save("a.png")
        manifest = self.root/"manifest.jsonl"
        prepare(self.images, manifest)
        config = Config(export_max_edge=64)
        analyze(manifest, self.root/"shards", config)
        merge(self.root/"shards", self.root/"out")
        result = export_images(self.root/"out", self.root/"export", workers=2)
        self.assertEqual(result, {"ok": 1})
        exported = self.rows(self.root/"export", "exported")[0]
        with Image.open(self.root/"export"/exported["path"]) as image:
            self.assertEqual(image.size, (64, 48))
            self.assertEqual(image.mode, "RGB")
        self.assertEqual(export_images(self.root/"out", self.root/"export", resume=True), {"ok": 1})
        path.write_bytes(b"changed")
        self.assertEqual(export_images(self.root/"out", self.root/"export", resume=True), {"error": 1})

    def test_hash_band_candidate_recall(self):
        rng = random.Random(34)
        for radius in range(9):
            for _ in range(20):
                value = rng.getrandbits(64)
                neighbor = value
                for bit in rng.sample(range(64), radius):
                    neighbor ^= 1 << bit
                original_bands = set(hash_bands(f"{value:016x}", radius))
                near_bands = set(hash_bands(f"{neighbor:016x}", radius))
                self.assertTrue(original_bands & near_bands)

    def test_invalid_config_and_frozen_manifest(self):
        with self.assertRaises(ValueError):
            Config(min_aspect_ratio=2, max_aspect_ratio=1)
        with self.assertRaises(ValueError):
            Config(phash_distance=12)
        self.save("one.png")
        manifest = self.root/"manifest.jsonl"
        prepare(self.images, manifest)
        with self.assertRaisesRegex(ValueError, "identity"):
            analyze(manifest, self.root/"shards", Config())
            analyze(manifest, self.root/"shards", Config(min_short_edge=100), resume=True)
        manifest.write_text(manifest.read_text(encoding="utf-8")+"\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            analyze(manifest, self.root/"new_shards", Config())

    def test_more_than_one_dedup_batch(self):
        path = self.save("one.png")
        raw = self.root/"raw.jsonl"
        raw.write_text("".join(json.dumps({"path": str(path), "text": str(i)})+"\n" for i in range(503)), encoding="utf-8")
        manifest = self.root/"manifest.jsonl"
        prepare(raw, manifest)
        analyze(manifest, self.root/"shards", Config(near_deduplicate=False))
        result = merge(self.root/"shards", self.root/"out")
        self.assertEqual(result["kept_count"], 503)

    def test_small_images_and_text_normalization(self):
        path = self.save("tiny.png", Image.new("RGB", (1, 1), "red"))
        row = self.row(path)
        self.assertEqual(row["status"], "candidate")
        self.assertIsNone(row["blur_score"])
        self.assertEqual(normalize_text(" e\u0301 \r\nsecond  ", "NFC"), "é\nsecond")

    def test_near_duplicate_recompression(self):
        # Smooth spatial content survives recompression with perceptual/color guards.
        y, x = np.indices((256, 256))
        data = np.stack([(x % 256), (y % 256), ((x+y)//2 % 256)], axis=-1).astype(np.uint8)
        image = Image.fromarray(data)
        self.save("one.png", image)
        image.save(self.images/"two.jpg", quality=92)
        manifest = self.root/"manifest.jsonl"
        prepare(self.images, manifest)
        analyze(manifest, self.root/"shards", Config())
        result = merge(self.root/"shards", self.root/"out")
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(self.rows(self.root/"out", "duplicate")[0]["duplicate_kind"], "near_perceptual")

    def test_resolution_lower_bound_after_statistics_and_cli_override(self):
        from image_cleaner.cli import dispatch, parser
        config = Config(deduplicate=False, near_deduplicate=False)
        for i, size in enumerate([(256, 256), (384, 256), (256, 384), (255, 384), (257, 384), (512, 512)]):
            self.save(f"{i}.png", Image.new("RGB", size, (10+i*20, 20, 30)))
        manifest = self.root/"manifest.jsonl"
        prepare(self.images, manifest)
        analyze(manifest, self.root/"shards", config, workers=2)
        merge(self.root/"shards", self.root/"statistics")
        args = parser().parse_args(["filter", "--shards", str(self.root/"shards"),
                                   "--output", str(self.root/"only_256p"), "--resolution", "256p"])
        result = dispatch(args)
        self.assertEqual(result["kept_count"], 5)
        self.assertEqual(result["rejected_count"], 1)
        self.assertTrue(all(r["short_edge"] >= 256 for r in self.rows(self.root/"only_256p", "kept")))
        self.assertTrue(all("resolution_mismatch" in r["reasons"]
                            for r in self.rows(self.root/"only_256p", "rejected")))
        report = json.loads((self.root/"only_256p"/"report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["stage_metrics"]["resolution_matched_count"], 5)
        self.assertEqual(report["stage_metrics"]["resolution_pass_rate"], 5/6)
        self.assertEqual(report["resolution_filter"]["mode"], "short_edge_gte")
        self.assertEqual(report["resolution_filter"]["comparison"], ">=")
        config_path = self.root/"resolution.json"
        config_path.write_text(json.dumps({**config.to_dict(), "resolution": "512p"}), encoding="utf-8")
        args = parser().parse_args(["merge", "--shards", str(self.root/"shards"),
                                   "--output", str(self.root/"only_square"), "--config", str(config_path),
                                   "--resolution", "256x256"])
        self.assertEqual(dispatch(args)["kept_count"], 1)
        self.assertEqual(self.rows(self.root/"only_square", "kept")[0]["width"], 256)
        self.assertEqual(merge(self.root/"shards", self.root/"reset", Config(**{
            **config.to_dict(), "resolution": "256p"}), resolution="any")["kept_count"], 6)

    def test_resolution_validation_and_rule_intersection(self):
        from image_cleaner.images import filter_reasons
        for bad in ["256", "0p", "0x256", "256x0", "256.0p", "256p,512p", 256, "-256p"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Config(resolution=bad)
        self.assertEqual(Config(resolution=" 256P ").resolution, "256p")
        self.assertEqual(Config(resolution="256\u00d7384").resolution, "256x384")
        path = self.save("match.png", Image.new("RGB", (257, 384), "blue"))
        row = self.row(path, Config(resolution="256p", min_short_edge=300))
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["reasons"], ["min_short_edge"])
        self.assertEqual(filter_reasons(row, Config(resolution="384x256")), ["resolution_mismatch"])

    def test_resolution_refilter_requires_no_source_reads(self):
        path = self.save("match.png", Image.new("RGB", (384, 256), "blue"))
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (256, 384), "red").save(self.images/"rotated.jpg", exif=exif)
        manifest = self.root/"manifest.jsonl"
        prepare(self.images, manifest)
        config = Config(deduplicate=False)
        analyze(manifest, self.root/"shards", config, rank=0, world_size=2)
        analyze(manifest, self.root/"shards", config, rank=1, world_size=2)
        # Filtering should use dimensions already stored in the analysis database.
        for image in self.images.iterdir():
            image.unlink()
        result = merge(self.root/"shards", self.root/"out", resolution="384x256")
        self.assertEqual(result["kept_count"], 2)
        self.assertEqual(result["rejected_count"], 0)
        self.assertTrue(all((r["width"], r["height"]) == (384, 256) for r in self.rows(self.root/"out", "kept")))


if __name__ == "__main__":
    unittest.main()

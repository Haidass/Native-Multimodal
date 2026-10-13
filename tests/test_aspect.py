import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from image_cleaner.aspect import assess_aspect
from image_cleaner.config import ASPECT_BUCKETS, Config
from image_cleaner.cli import main
from image_cleaner.images import filter_reasons
from image_cleaner.io import prepare, manifest_items
from image_cleaner.jobs import analyze, merge


class AspectTests(unittest.TestCase):
    def match(self, width, height, **kwargs):
        return assess_aspect({"width": width, "height": height}, Config(**kwargs))

    def test_strict_presets_orientation_and_all(self):
        for bucket in ASPECT_BUCKETS:
            w, h = map(int, bucket.split(":"))
            self.assertEqual(self.match(w*20, h*20, aspect_buckets="all")["aspect_bucket"], bucket)
        self.assertFalse(self.match(60, 40, aspect_buckets="2:3")["aspect_bucket_match"])
        self.assertFalse(self.match(9, 21, aspect_buckets="all")["aspect_bucket_match"])
        self.assertFalse(self.match(1001, 1000, aspect_buckets="1:1")["aspect_bucket_match"])

    def test_inclusive_symmetric_boundary_and_custom_tolerance(self):
        for w, h in [(1050, 1000), (1000, 1050)]:
            row = self.match(w, h, aspect_buckets="1:1", aspect_mode="loose")
            self.assertTrue(row["aspect_bucket_match"])
            self.assertEqual(row["aspect_bucket_deviation"], .05)
        for w, h in [(1051, 1000), (1000, 1051)]:
            self.assertFalse(self.match(w, h, aspect_buckets="1:1", aspect_mode="loose")["aspect_bucket_match"])
        self.assertTrue(self.match(1100, 1000, aspect_buckets="1:1", aspect_mode="loose", aspect_tolerance=.1)["aspect_bucket_match"])
        self.assertFalse(self.match(1001, 1000, aspect_buckets="1:1", aspect_mode="loose", aspect_tolerance=0)["aspect_bucket_match"])

    def test_three_to_one_and_one_to_three_boundaries(self):
        self.assertEqual(len(Config(aspect_buckets="all").aspect_buckets), 10)
        for bucket, width, height in [("1:3", 300, 900), ("3:1", 900, 300)]:
            self.assertEqual(self.match(width, height, aspect_buckets=bucket)["aspect_bucket"], bucket)
            self.assertFalse(self.match(height, width, aspect_buckets=bucket)["aspect_bucket_match"])
        for width, height, bucket in [(315, 900, "1:3"), (900, 315, "3:1"),
                                       (300, 945, "1:3"), (945, 300, "3:1")]:
            result = self.match(width, height, aspect_buckets=bucket, aspect_mode="loose")
            self.assertTrue(result["aspect_bucket_match"])
            self.assertEqual(result["aspect_bucket_deviation"], .05)
        for width, height, bucket in [(316, 900, "1:3"), (900, 316, "3:1"),
                                       (299, 945, "1:3"), (945, 299, "3:1")]:
            self.assertFalse(self.match(width, height, aspect_buckets=bucket, aspect_mode="loose")["aspect_bucket_match"])
        self.assertEqual(Config(aspect_buckets="3:1,1:3,1:1").aspect_buckets, ("1:1", "1:3", "3:1"))

    def test_new_buckets_cached_cli_and_report(self):
        temp_root = Path(__file__).resolve().parents[1]/"runs"/"test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            root = Path(temp)
            images = root/"images"
            images.mkdir()
            for i, size in enumerate([(300, 900), (900, 300), (315, 900),
                                      (900, 315), (316, 900), (900, 316)]):
                Image.new("RGB", size, "blue").save(images/f"{i}.png")
            manifest = root/"input.jsonl"
            prepare(images, manifest)
            analyze(manifest, root/"stats", Config(deduplicate=False, near_deduplicate=False), workers=2)
            for image in images.iterdir():
                image.unlink()
            for mode, kept in [("strict", 2), ("loose", 4)]:
                output = root/mode
                self.assertEqual(main(["filter", "--shards", str(root/"stats"), "--output", str(output),
                    "--aspect-buckets", "1:3,3:1", "--aspect-mode", mode, "--resolution", "256p"]), 0)
                report = json.loads((output/"report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["kept_count"], kept)
                self.assertEqual(report["aspect_bucket_filter"]["counts_after"], {"1:3": kept//2, "3:1": kept//2})

    def test_overlap_assignment_and_tie(self):
        self.assertEqual(self.match(1400, 1000, aspect_buckets="3:2,4:3", aspect_mode="loose", aspect_tolerance=.2)["aspect_bucket"], "4:3")
        self.assertEqual(self.match(100, 100, aspect_buckets="3:2,2:3", aspect_mode="loose", aspect_tolerance=.5)["aspect_bucket"], "2:3")

    def test_validation_and_normalization(self):
        for value in ["", "1:1,", [], "4:5", ["all"], 5]:
            with self.assertRaises(ValueError):
                Config(aspect_buckets=value)
        for value in [None, True, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                Config(aspect_tolerance=value)
        self.assertEqual(Config(aspect_buckets="3:2,1:1,1:1").aspect_buckets, ("1:1", "3:2"))
        self.assertIsNone(Config(aspect_buckets="none").aspect_buckets)
        with self.assertRaises(ValueError):
            Config(aspect_mode="invalid")

    def test_disabled_clears_stale_assignment(self):
        row = {"width": 100, "height": 100, "short_edge": 100, "aspect_bucket": "1:1"}
        self.assertEqual(filter_reasons(row, Config()), [])
        self.assertIsNone(row["aspect_bucket"])
        self.assertIsNone(row["aspect_bucket_match"])

    def test_sharded_cached_filter_cli_and_reports(self):
        temp_root = Path(__file__).resolve().parents[1]/"runs"/"test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            root = Path(temp)
            images = root/"images"
            images.mkdir()
            for i, size in enumerate([(256, 256), (315, 300), (316, 300), (300, 450), (128, 128)]):
                Image.new("RGB", size, "blue").save(images/f"{i}.png")
            (images/"broken.jpg").write_bytes(b"broken")
            manifest = root/"input.jsonl"
            prepare(images, manifest)
            config = Config(deduplicate=False, near_deduplicate=False)
            analyze(manifest, root/"single", config, workers=2)
            for rank in range(2):
                analyze(manifest, root/"shards", config, workers=2, rank=rank, world_size=2)
            # Remove source bytes: repeated filtering must use cached measurements only.
            for path in images.iterdir():
                path.unlink()
            args = ["filter", "--shards", str(root/"shards"), "--output", str(root/"loose"),
                    "--aspect-buckets", "1:1,2:3", "--aspect-mode", "loose", "--resolution", "256p"]
            self.assertEqual(main(args), 0)
            report = json.loads((root/"loose"/"report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["kept_count"], 3)
            self.assertEqual(report["stage_metrics"]["aspect_bucket_matched_count"], 4)
            self.assertEqual(report["stage_metrics"]["aspect_bucket_pass_rate"], .8)
            self.assertEqual(report["aspect_bucket_filter"]["counts_before"], {"1:1": 3, "2:3": 1})
            self.assertEqual(report["aspect_bucket_filter"]["counts_after"], {"1:1": 2, "2:3": 1})
            merge(root/"single", root/"equivalent", resolution="256p", aspect_buckets="1:1,2:3", aspect_mode="loose")
            for status in ["kept", "rejected", "error", "duplicate"]:
                self.assertEqual((root/"loose"/f"{status}.jsonl").read_bytes(), (root/"equivalent"/f"{status}.jsonl").read_bytes())
            merge(root/"shards", root/"strict", aspect_buckets="1:1,2:3")
            strict = list(manifest_items(root/"strict"/"rejected.jsonl"))
            self.assertEqual(len(strict), 2)
            self.assertIn("aspect_bucket_mismatch", strict[0]["reasons"])
            self.assertIsNone(strict[0]["aspect_bucket"])
            self.assertIsNotNone(strict[0]["aspect_bucket_nearest"])
            args[args.index(str(root/"loose"))] = str(root/"tighter")
            self.assertEqual(main(args + ["--aspect-tolerance", "0.03"]), 0)
            tighter = json.loads((root/"tighter"/"report.json").read_text(encoding="utf-8"))
            self.assertEqual(tighter["kept_count"], 2)


if __name__ == "__main__":
    unittest.main()

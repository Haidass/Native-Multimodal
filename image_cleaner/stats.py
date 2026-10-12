from collections import Counter
import html
import json
import math
import random

import numpy as np

from .io import atomic_text


BINS = {
    "width": [0, 224, 512, 768, 1024, 2048, 4096, 8192, math.inf],
    "height": [0, 224, 512, 768, 1024, 2048, 4096, 8192, math.inf],
    "short_edge": [0, 224, 512, 768, 1024, 2048, 4096, 8192, math.inf],
    "long_edge": [0, 224, 512, 768, 1024, 2048, 4096, 8192, math.inf],
    "pixels": [0, 224**2, 512**2, 1024**2, 2048**2, 4096**2, 8192**2, math.inf],
    "aspect_ratio": [0, .25, .5, .75, .95, 1.05, 1.33, 2, 4, math.inf],
    "blur_score": [0, 5, 20, 50, 100, 200, 500, 1000, 5000, math.inf],
    "noise_sigma": [0, .5, 1, 2, 4, 8, 16, 32, math.inf],
    "snr_proxy_db": [-math.inf, 0, 10, 20, 30, 40, 50, 60, math.inf],
    "blockiness": [0, .5, 1, 2, 4, 8, 16, 32, math.inf],
    "text_chars": [0, 1, 10, 30, 100, 300, 1000, 3000, math.inf],
}


class Distribution:
    def __init__(self, bins, seed=17, capacity=20_000):
        self.bins = bins
        self.hist = [0] * (len(bins)-1)
        self.n = 0
        self.mean = self.m2 = 0.0
        self.low = math.inf
        self.high = -math.inf
        self.sample = []
        self.random = random.Random(seed)
        self.capacity = capacity
        self.missing = 0

    def add(self, value):
        if value is None or not math.isfinite(value):
            self.missing += 1
            return
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)
        self.low, self.high = min(self.low, value), max(self.high, value)
        slot = int(np.searchsorted(self.bins, value, side="right")) - 1
        self.hist[max(0, min(slot, len(self.hist)-1))] += 1
        if len(self.sample) < self.capacity:
            self.sample.append(value)
        else:
            slot = self.random.randrange(self.n)
            if slot < self.capacity:
                self.sample[slot] = value

    def result(self):
        def boundary(value):
            return "-inf" if value == -math.inf else "inf" if value == math.inf else value
        percentiles = [1, 5, 25, 50, 75, 95, 99]
        quantiles = np.percentile(self.sample, percentiles).tolist() if self.sample else [None]*7
        return {"count": self.n, "missing": self.missing, "min": self.low if self.n else None,
                "max": self.high if self.n else None, "mean": self.mean if self.n else None,
                "std": math.sqrt(self.m2/self.n) if self.n else None,
                "quantiles": dict(zip([f"p{p}" for p in percentiles], quantiles)),
                "quantiles_approximate": self.n > self.capacity, "quantile_sample_size": len(self.sample),
                "histogram": [{"left": boundary(a), "right": boundary(b), "count": c}
                              for a, b, c in zip(self.bins[:-1], self.bins[1:], self.hist)]}


class DatasetStats:
    def __init__(self):
        self.distributions = {name: Distribution(bins) for name, bins in BINS.items()}
        self.total = self.decoded = 0
        self.statuses, self.reasons, self.formats, self.orientations = [Counter() for _ in range(4)]

    def add(self, row):
        self.total += 1
        self.statuses[row["status"]] += 1
        self.reasons.update(row.get("reasons", []))
        if "width" not in row or row["status"] == "error":
            return
        self.decoded += 1
        self.formats[row["original_format"]] += 1
        ratio = row["aspect_ratio"]
        self.orientations["square" if .95 <= ratio <= 1.05 else "landscape" if ratio > 1 else "portrait"] += 1
        for name, distribution in self.distributions.items():
            value = len(row.get("text") or "") if name == "text_chars" else row.get(name)
            distribution.add(value)

    def result(self):
        return {"total": self.total, "decoded": self.decoded, "statuses": dict(self.statuses),
                "reasons": dict(self.reasons), "formats": dict(self.formats),
                "orientations": dict(self.orientations),
                "distributions": {name: d.result() for name, d in self.distributions.items()}}


def write_html(path, report):
    labels = {"width": "宽度", "height": "高度", "short_edge": "短边", "long_edge": "长边",
              "pixels": "总像素数", "aspect_ratio": "宽高比 W/H", "blur_score": "清晰度代理（拉普拉斯方差）",
              "noise_sigma": "噪声估计 σ", "snr_proxy_db": "信噪比代理 dB", "blockiness": "块效应代理",
              "text_chars": "文本字符数"}
    with atomic_text(path) as handle:
        handle.write('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>图片清洗报告</title>'
                     '<style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 20px;'
                     'color:#1e293b;background:#f8fafc}section{background:white;padding:20px;margin:20px 0;'
                     'border-radius:12px}svg{width:100%;max-height:260px}pre{white-space:pre-wrap}'
                     '.legend{color:#2563eb}text{font:11px system-ui}</style><h1>图片清洗报告</h1>')
        handle.write('<p>原始分布为成功解码后的标准化方向；蓝色为原始数据，绿色为筛选保留数据。'
                     '直方图为精确计数，分位数最多使用 20,000 条蓄水池样本。噪声、信噪比和块效应为代理指标。</p>')
        summary = {k: report[k] for k in ["input_count", "kept_count", "rejected_count", "duplicate_count", "error_count"]}
        handle.write('<section><pre>' + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre></section>')
        if "stage_metrics" in report:
            handle.write('<section><h2>分辨率筛选与阶段指标</h2><pre>' + html.escape(json.dumps(
                {"resolution_filter": report["resolution_filter"], "stage_metrics": report["stage_metrics"]},
                ensure_ascii=False, indent=2)) + '</pre></section>')
        if "aspect_bucket_filter" in report:
            handle.write('<section><h2>宽高比桶筛选</h2><pre>' + html.escape(json.dumps(
                report["aspect_bucket_filter"], ensure_ascii=False, indent=2)) + '</pre></section>')
        for name, label in labels.items():
            original = report["before"]["distributions"][name]
            retained = report["after"]["distributions"][name]
            bins, kept = original["histogram"], retained["histogram"]
            peak = max(1, max(b["count"] for b in bins))
            width = 900 / len(bins)
            handle.write('<section><h2>' + label + '</h2><svg viewBox="0 0 960 260" role="img" aria-label="' + label + '分布">')
            for i, (b, k) in enumerate(zip(bins, kept)):
                x = 30 + width*i
                for offset, count, color in [(0, b["count"], "#2563eb"), (.4, k["count"], "#16a34a")]:
                    height = 180*count/peak
                    handle.write(f'<rect x="{x+width*offset:.1f}" y="{205-height:.1f}" width="{width*.35:.1f}" '
                                 f'height="{height:.1f}" fill="{color}"><title>{count}</title></rect>')
                text = html.escape(f'{b["left"]}–{b["right"]}')
                handle.write(f'<text x="{x:.1f}" y="228" transform="rotate(12 {x:.1f} 228)">{text}</text>')
            handle.write('</svg><p class="legend">蓝：原始　绿：保留</p><pre>')
            handle.write(html.escape(json.dumps({"before": original["quantiles"], "after": retained["quantiles"]},
                                               ensure_ascii=False, indent=2)))
            handle.write('</pre></section>')
        handle.write('<section><h2>筛选原因</h2><pre>' + html.escape(json.dumps(report["before"]["reasons"],
                     ensure_ascii=False, indent=2)) + '</pre></section></html>')

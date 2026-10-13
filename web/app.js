"use strict";
const ASPECT_BUCKET_PRESETS = ["1:1","2:3","3:2","3:4","4:3","9:16","16:9","21:9","1:3","3:1"];
// Use integer rational comparisons in the browser calculator, like the CLI.
function decimalFraction(value) {
  const parts = String(value).toLowerCase().split("e");
  const mantissa = parts[0].split(".");
  let numerator = BigInt(mantissa.join(""));
  const exponent = Number(parts[1] || 0) - (mantissa[1] || "").length;
  let denominator = 1n;
  if (exponent >= 0) numerator *= 10n ** BigInt(exponent);
  else denominator = 10n ** BigInt(-exponent);
  return [numerator, denominator];
}
function bucketAssessment(width, height, buckets, mode, percent) {
  let best = null;
  const [tn, td] = decimalFraction(percent);
  for (const bucket of buckets) {
    const [n, d] = bucket.split(":").map(BigInt);
    const a = BigInt(width) * d, b = BigInt(height) * n;
    const difference = a >= b ? a - b : b - a, base = a <= b ? a : b;
    const matched = mode === "strict" ? difference === 0n : difference * td * 100n <= base * tn;
    if (best === null || difference * best.base < best.difference * base) {
      best = {bucket, difference, base, matched};
    }
  }
  return best;
}
function buildFilterCommand(platform, resolution, buckets, mode, percent) {
  if (!/^(?:[1-9][0-9]*p|[1-9][0-9]*x[1-9][0-9]*|any|none)$/.test(resolution)) {
    throw new Error("分辨率请填写 256p、384x256 或 any；数字需为正整数。");
  }
  if (mode === "loose" && (!Number.isFinite(Number(percent)) || Number(percent) < 0 || String(percent).trim() === "")) {
    throw new Error("宽松偏差需为有限非负数，例如 5 表示 5%。");
  }
  const python = platform === "windows" ? ".\\.venv\\Scripts\\python.exe" : ".venv/bin/python";
  const allSelected = buckets.length === ASPECT_BUCKET_PRESETS.length && ASPECT_BUCKET_PRESETS.every(bucket => buckets.includes(bucket));
  const selected = allSelected ? "all" : buckets.length ? '"' + buckets.join(",") + '"' : "none";
  let command = `${python} -m image_cleaner filter --shards runs/statistics/shards --output runs/filtered --resolution ${resolution} --aspect-buckets ${selected}`;
  if (buckets.length) {
    command += ` --aspect-mode ${mode}`;
    if (mode === "loose") command += ` --aspect-tolerance ${Number(percent) / 100}`;
  }
  return command;
}
if (typeof module !== "undefined") module.exports = {bucketAssessment, buildFilterCommand};
if (typeof document !== "undefined") {
  document.querySelectorAll("table").forEach(table => {
    const wrapper = document.createElement("div"); wrapper.className = "table-scroll";
    table.parentNode.insertBefore(wrapper, table); wrapper.appendChild(table);
  });
  document.querySelectorAll("pre").forEach(pre => {
    const button = document.createElement("button"); button.className = "copy-code";
    button.type = "button"; button.textContent = "复制";
    button.addEventListener("click", async () => {
      const value = pre.querySelector("code") ? pre.querySelector("code").textContent : pre.textContent.replace(/复制$/, "");
      try {
        await navigator.clipboard.writeText(value); button.textContent = "已复制";
      } catch (_) { button.textContent = "请选中后复制"; }
      setTimeout(() => {button.textContent = "复制";}, 2000);
    });
    pre.appendChild(button);
  });
  if (document.getElementById("command")) {
    const ids = ["platform", "resolution", "mode", "tolerance", "width", "height"];
    const controls = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
    const buckets = [...document.querySelectorAll('input[name="bucket"]')];
    const update = () => {
      const selected = buckets.filter(x => x.checked).map(x => x.value);
      const mode = controls.mode.value, percent = controls.tolerance.value;
      controls.tolerance.disabled = mode === "strict";
      const error = document.getElementById("command-error"), output = document.getElementById("command");
      try {
        output.textContent = buildFilterCommand(controls.platform.value, controls.resolution.value.trim().toLowerCase(), selected, mode, percent);
        error.textContent = "";
      } catch (e) { output.textContent = "请先修正参数。"; error.textContent = e.message; }
      const result = document.getElementById("ratio-result"), w = Number(controls.width.value), h = Number(controls.height.value);
      if (!Number.isSafeInteger(w) || !Number.isSafeInteger(h) || w < 1 || h < 1) {
        result.textContent = "请输入大于零的整数宽高。"; return;
      }
      if (error.textContent) { result.textContent = "请先修正筛选参数。"; return; }
      const best = bucketAssessment(w, h, selected, mode, mode === "strict" ? "0" : percent);
      const resolution = controls.resolution.value.trim().toLowerCase();
      let sizeMatched = true;
      if (resolution.endsWith("p")) sizeMatched = Math.min(w, h) >= Number(resolution.slice(0, -1));
      else if (resolution.includes("x")) {
        const [rw, rh] = resolution.split("x").map(Number); sizeMatched = w === rw && h === rh;
      }
      const bucketMessage = best ? `${best.matched ? "桶条件通过" : "桶条件未通过"}；最近桶 ${best.bucket}，偏差 ${(Number(best.difference) / Number(best.base) * 100).toFixed(3)}%` : "桶条件已关闭";
      result.textContent = `${w}×${h}：${bucketMessage}；分辨率条件${sizeMatched ? "通过" : "未通过"}。`;
    };
    [...Object.values(controls), ...buckets].forEach(x => x.addEventListener("input", update));
    document.getElementById("select-all").addEventListener("click", () => {buckets.forEach(x => x.checked = true); update();});
    document.getElementById("select-none").addEventListener("click", () => {buckets.forEach(x => x.checked = false); update();});
    update();
  }
}

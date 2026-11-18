/* Pyodide 桥：把原本跑在 Python 服务器上的排版逻辑搬进浏览器。
   对 app.js 完全透明——它还是照常 fetch("api/render")，这里把请求截下来交给 Python。 */
(() => {
  // 国内 jsdelivr 经常不稳，依次试几个源
  const CDN_LIST = [
    "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/",
    "https://fastly.jsdelivr.net/pyodide/v0.27.7/full/",
    "https://unpkg.com/pyodide@0.27.7/",
  ];
  const PY_FILES = ["__init__", "planner", "renderer", "fonts", "palettes", "cutout", "doodles", "sticker", "decor"];
  const FONT_FILE = "乐米蜡笔体.ttf";

  let py = null;
  let readyResolve;
  const ready = new Promise((r) => (readyResolve = r));

  const boot = document.getElementById("boot");
  const bootText = document.getElementById("bootText");
  const bootBar = document.getElementById("bootBar");

  function status(text, pct) {
    if (bootText) bootText.textContent = text;
    if (bootBar && pct) bootBar.style.width = pct + "%";
  }

  function fail(msg) {
    if (bootText) {
      bootText.textContent = "启动失败：" + msg;
      bootText.style.color = "#e08a7a";
    }
    const retry = document.getElementById("bootRetry");
    if (retry) retry.hidden = false;
    if (bootBar) bootBar.style.background = "#7a3b33";
    if (boot) boot.dataset.error = "1";
  }

  function writeFile(path, data) {
    const parts = path.split("/");
    parts.pop();
    if (parts.length) py.FS.mkdirTree(parts.join("/"));
    py.FS.writeFile(path, data);
  }

  async function fetchBytes(url, onProgress) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(url + " -> HTTP " + res.status);
    const total = Number(res.headers.get("Content-Length") || 0);
    if (!res.body || !total || !onProgress) {
      return new Uint8Array(await res.arrayBuffer());
    }
    const reader = res.body.getReader();
    const chunks = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      onProgress(got / total);
    }
    const out = new Uint8Array(got);
    let off = 0;
    for (const c of chunks) {
      out.set(c, off);
      off += c.length;
    }
    return out;
  }

  async function loadFontBytes() {
    const name = encodeURIComponent(FONT_FILE);
    const gzUrl = `assets/fonts/${name}.gz`;
    try {
      let bytes = await fetchBytes(gzUrl, (p) => {
        status(`载入手写字体 ${Math.round(p * 100)}%（5MB）`, 70 + p * 20);
      });
      const enc = "gzip";
      if (typeof DecompressionStream === "function") {
        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream(enc));
        bytes = new Uint8Array(await new Response(stream).arrayBuffer());
      }
      if (bytes.length > 1e6) return bytes;
    } catch (err) {
      console.warn("gz 字体失败，改用原始文件", err);
    }
    status("载入手写字体（12MB，可能较慢）…", 70);
    return await fetchBytes(`assets/fonts/${name}`, (p) => {
      status(`载入手写字体 ${Math.round(p * 100)}%`, 70 + p * 20);
    });
  }

  const BOOT_PY = `
import sys, io, json, base64
sys.path.insert(0, "/home/pyodide")
from PIL import Image
from journal import planner, renderer, Options

RATIOS = {"3:4": (1200, 1600), "2:3": (1200, 1800), "1:1": (1200, 1200), "9:16": (1080, 1920)}

def _decode(data_url):
    if not data_url:
        return None
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(data_url)))
    img.load()
    icc = img.info.get("icc_profile")
    if icc:
        try:
            from PIL import ImageCms
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            img = ImageCms.profileToProfile(img, src, dst, outputMode="RGB")
        except Exception:
            pass
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if max(img.size) > 2400:
        s = 2400 / max(img.size)
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    return img

def render_page(payload_json):
    a = json.loads(payload_json)
    img = _decode(a.get("image") or "")
    text = (a.get("text") or "").strip()
    title = (a.get("title") or "").strip() or None
    palette = a.get("palette") or "ink"
    seed = int(a.get("seed") or 0)
    ratio = a.get("ratio") or "2:3"
    mask_png = None
    cut = a.get("cutout") or ""
    if cut and "," in cut:
        mask_png = base64.b64decode(cut.split(",", 1)[1])

    p = planner.plan(text, title=title, palette=palette, seed=seed)
    w, h = RATIOS.get(ratio, (1200, 1800))
    opts = Options(
        width=w, height=h,
        palette=p.palette if palette in ("", "suggest") else palette,
        photo_style=a.get("style") or "card",
        layout=a.get("layout") or "center",
        decor=a.get("decor") or "medium",
        seed=seed,
        tolerance=float(a.get("tolerance") or 30),
        doodles=bool(a.get("doodles", True)),
        font=a.get("font") or "lemi",
        emoji=bool(a.get("emoji", True)),
        cutout_force=bool(a.get("cutout_force")),
        mask_png=mask_png,
        tag_style=a.get("tag_style") or "sticky",
    )
    out, meta = renderer.render(p, img, opts)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return json.dumps({
        "png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
        "meta": meta,
        "plan": p.to_dict(),
    }, ensure_ascii=False, default=str)

def cutout_page(payload_json):
    from journal.cutout import remove_background
    a = json.loads(payload_json)
    img = _decode(a.get("image") or "")
    if img is None:
        return json.dumps({"error": "没有图片"}, ensure_ascii=False)
    cut, score, info = remove_background(img, float(a.get("tolerance") or 30), trim=False)
    if cut is None:
        cut = img.convert("RGBA")
        info = dict(info or {})
        info["fallback"] = True
    if max(cut.size) > 900:
        s = 900 / max(cut.size)
        cut = cut.resize((int(cut.width * s), int(cut.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    cut.save(buf, "PNG")
    return json.dumps({
        "cutout": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
        "score": round(float(score), 3),
        "info": info,
        "size": list(cut.size),
    }, ensure_ascii=False, default=str)
`;

  function loadScript(src) {
    return new Promise((res, rej) => {
      const el = document.createElement("script");
      el.src = src;
      el.onload = () => res(true);
      el.onerror = () => rej(new Error("加载失败 " + src));
      document.head.appendChild(el);
    });
  }

  async function pickCdn() {
    for (const base of CDN_LIST) {
      try {
        if (!window.loadPyodide) {
          status("下载 Python 运行时…", 8);
          await loadScript(base + "pyodide.js");
        }
        if (!window.loadPyodide) throw new Error("loadPyodide 未定义");
        const r = await fetch(base + "pyodide-lock.json", { cache: "force-cache" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        return base;
      } catch (err) {
        console.warn("源不可用，换下一个：", base, err);
      }
    }
    throw new Error("所有 Python 运行时的下载源都不可用（网络被拦？）");
  }

  async function boot_() {
    try {
      const base = await pickCdn();
      status("启动 Python 运行时…", 18);
      py = await loadPyodide({ indexURL: base });

      status("加载 Pillow / numpy…", 35);
      await py.loadPackage(["pillow", "numpy"]);

      status("载入排版程序…", 55);
      for (const name of PY_FILES) {
        const text = await (await fetch(`py/journal/${name}.py`)).text();
        writeFile(`/home/pyodide/journal/${name}.py`, text);
      }

      const fontBytes = await loadFontBytes();
      writeFile(`/home/pyodide/assets/fonts/${FONT_FILE}`, fontBytes);

      status("初始化…", 92);
      py.runPython(BOOT_PY);

      status("就绪", 100);
      readyResolve(py);
      if (boot) boot.hidden = true;
    } catch (err) {
      console.error(err);
      fail(err && err.message ? err.message : String(err));
      readyResolve(null);
    }
  }

  /* ---------- 把 fetch 的 API 调用接给 Python ---------- */
  const realFetch = window.fetch.bind(window);

  function jsonResponse(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  async function callPy(fnName, payload) {
    await ready;
    if (!py) throw new Error("python not ready");
    // 让浏览器先把 loading 态画出来，再进入同步计算
    await new Promise((r) => setTimeout(r, 40));
    const fn = py.globals.get(fnName);
    let out;
    try {
      out = fn(payload);
    } finally {
      if (fn && fn.destroy) fn.destroy();
    }
    return out;
  }

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const body = (init && init.body) || "{}";

    if (url === "api/render") {
      const data = await callPy("render_page", body);
      return jsonResponse(JSON.parse(data));
    }
    if (url === "api/cutout") {
      const data = await callPy("cutout_page", body);
      return jsonResponse(JSON.parse(data));
    }
    if (url === "api/styles") return jsonResponse({ styles: [] });
    if (url === "api/info") return jsonResponse({ lan: [] });
    if (url.indexOf("api/bubbles") === 0) return jsonResponse({ bubbles: [] });
    if (url === "api/stickers") {
      return jsonResponse({ stickers: window.STICKER_MANIFEST || [] });
    }
    return realFetch(input, init);
  };

  boot_();
})();

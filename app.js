(() => {
  const $ = (s) => document.querySelector(s);

  const SWATCHES = [
    ["ink", "#0F0F12", "墨黑"],
    ["dusk", "#1E2733", "暮蓝"],
    ["forest", "#16231C", "深林"],
    ["wine", "#241a22", "夜莓"],
    ["paper", "#F7F3E9", "米白"],
    ["butter", "#FBF1CE", "黄油"],
    ["blush", "#F7E8E4", "藕粉"],
    ["sage", "#E8EEE2", "鼠尾草"],
    ["sky", "#E3EAF0", "雾蓝"],
    ["cream", "#F6EDE0", "奶油"],
  ];

  const state = {
    image: null,
    text: "",
    palette: "suggest",
    style: "card",
    decor: "medium",
    ratio: "2:3",
    tolerance: 30,
    doodles: true,
    emoji: true,
    font: "lemi",
    layout: "auto",
    tagStyle: "sticky",
    bubble: "b11",
    placed: [],   // {name,url,x,y,size,rot} 归一化坐标
    sel: -1,
    force: false,
    cutoutPng: null,
    seed: Math.floor(Math.random() * 1000),
    png: null,
    busy: false,
    reqId: 0,
  };

  /* ---------------- 配色圆点 ---------------- */
  const swWrap = $("#swatches");
  SWATCHES.forEach(([key, hex, name]) => {
    const b = document.createElement("button");
    b.className = "sw";
    b.type = "button";
    b.dataset.v = key;
    b.style.background = hex;
    b.title = name;
    b.addEventListener("click", () => {
      state.palette = key;
      syncPalette();
      schedule();
    });
    swWrap.appendChild(b);
  });

  function syncPalette() {
    [...swWrap.children].forEach((el) => {
      el.classList.toggle("on", el.dataset.v === state.palette);
    });
    const hint = {
      suggest: "按文字情绪取色",
      auto: "按照片取色",
    }[state.palette];
    $("#paletteHint").textContent =
      hint || (state.palette.startsWith("#") ? "自定义颜色" : "纯色底，可随时换");
  }

  $("#autoBtn").addEventListener("click", () => {
    state.palette = "auto";
    syncPalette();
    schedule();
  });

  $("#textBtn").addEventListener("click", () => {
    state.palette = "suggest";
    syncPalette();
    schedule();
  });

  $("#color").addEventListener("input", (e) => {
    state.palette = e.target.value.toUpperCase();
    syncPalette();
    schedule();
  });

  /* ---------------- 分段控件 ---------------- */
  function bindSeg(sel, key, after) {
    $(sel).addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      state[key] = b.dataset.v;
      [...$(sel).children].forEach((el) => el.classList.toggle("on", el === b));
      after && after();
      schedule();
    });
  }
  bindSeg("#styles", "style", () => {
    $("#tolWrap").hidden = !["cutout", "sticker"].includes(state.style);
  });
  bindSeg("#decor", "decor");
  bindSeg("#ratio", "ratio");
  bindSeg("#fonts", "font");
  bindSeg("#layout", "layout");
  bindSeg("#tagStyle", "tagStyle");

  $("#tol").addEventListener("input", (e) => {
    state.tolerance = Number(e.target.value);
    $("#tolVal").textContent = state.tolerance;
    schedule();
  });

  $("#doodleBtn").addEventListener("click", () => {
    state.doodles = !state.doodles;
    $("#doodleBtn").classList.toggle("on", state.doodles);
    $("#doodleBtn").setAttribute("aria-pressed", String(state.doodles));
    schedule();
  });

  $("#emojiBtn").addEventListener("click", () => {
    state.emoji = !state.emoji;
    $("#emojiBtn").classList.toggle("on", state.emoji);
    $("#emojiBtn").setAttribute("aria-pressed", String(state.emoji));
    schedule();
  });

  $("#forceBtn").addEventListener("change", (e) => {
    state.force = e.target.checked;
    schedule();
  });

  /* ---------------- 抠图修边 ---------------- */
  const mk = {
    cv: null, ctx: null, src: null, cutUrl: null,
    w: 0, h: 0, tool: "erase", size: 26, drawing: false, last: null,
  };

  function loadDataUrl(url) {
    return new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error("图片加载失败"));
      i.src = url;
    });
  }

  async function setupMaskCanvas(cutUrl) {
    const [cut, src] = await Promise.all([loadDataUrl(cutUrl), loadDataUrl(state.image)]);
    const maxW = Math.min(720, Math.max(240, window.innerWidth - 100));
    const maxH = Math.max(200, Math.min(window.innerHeight * 0.54, 520));
    const scale = Math.min(maxW / cut.width, maxH / cut.height, 1);
    const w = Math.max(1, Math.round(cut.width * scale));
    const h = Math.max(1, Math.round(cut.height * scale));
    const cv = $("#mkCanvas");
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(cut, 0, 0, w, h);
    mk.cv = cv;
    mk.ctx = ctx;
    mk.src = src;
    mk.w = w;
    mk.h = h;
    mk.cutUrl = cutUrl;
    mk.last = null;
  }

  async function openMaskEditor() {
    if (!state.image) return toast("先放一张照片");
    $("#mk").hidden = false;
    $("#mkTip").textContent = "正在抠图…";
    try {
      if (state.cutoutPng) {                 // 之前修过，接着改
        await setupMaskCanvas(state.cutoutPng);
        $("#mkTip").textContent = "这是你上次修好的蒙版，继续擦或补；「重置」回到刚打开时的样子。";
        return;
      }
      const res = await fetch("api/cutout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: state.image, tolerance: state.tolerance }),
      });
      const d = await res.json();
      if (!res.ok || d.error) throw new Error(d.error || "抠图失败");
      await setupMaskCanvas(d.cutout);
      $("#mkTip").textContent = d.info && d.info.fallback
        ? "自动抠图没成功，先给你原图——手动擦掉背景就行（误删了用「补回」刷回来）"
        : "格子 = 会被去掉的地方。擦掉多余背景，或用「补回」把误删的主体刷回来。";
    } catch (err) {
      $("#mk").hidden = true;
      $("#mkTip").textContent = "";
      toast(err.message);
    }
  }

  function mkPos(e) {
    const r = mk.cv.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) * (mk.cv.width / r.width),
      y: (e.clientY - r.top) * (mk.cv.height / r.height),
    };
  }

  function mkPaint(x, y) {
    const c = mk.ctx;
    const r = mk.size / 2;
    c.save();
    if (mk.tool === "erase") {
      c.globalCompositeOperation = "destination-out";
      c.beginPath();
      c.arc(x, y, r, 0, Math.PI * 2);
      c.fill();
    } else {
      c.beginPath();
      c.arc(x, y, r, 0, Math.PI * 2);
      c.clip();
      c.drawImage(mk.src, 0, 0, mk.w, mk.h);
    }
    c.restore();
  }

  function mkStroke(a, b) {
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    const step = Math.max(2, mk.size * 0.25);
    const n = Math.max(1, Math.ceil(dist / step));
    for (let i = 1; i <= n; i++) {
      mkPaint(a.x + ((b.x - a.x) * i) / n, a.y + ((b.y - a.y) * i) / n);
    }
  }

  $("#openMask").addEventListener("click", openMaskEditor);
  $("#mkClose").addEventListener("click", () => { $("#mk").hidden = true; });
  $("#mkBrush").addEventListener("input", (e) => {
    mk.size = Number(e.target.value);
    $("#mkBrushVal").textContent = mk.size;
  });
  $("#mkTools").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b) return;
    mk.tool = b.dataset.v;
    [...$("#mkTools").children].forEach((el) => el.classList.toggle("on", el === b));
  });
  $("#mkReset").addEventListener("click", async () => {
    if (mk.cutUrl) await setupMaskCanvas(mk.cutUrl);
  });
  $("#mkApply").addEventListener("click", () => {
    if (!mk.cv) return;
    state.cutoutPng = mk.cv.toDataURL("image/png");
    $("#mk").hidden = true;
    generate();
  });

  const mkCanvas = $("#mkCanvas");
  mkCanvas.addEventListener("pointerdown", (e) => {
    if (!mk.ctx) return;
    e.preventDefault();
    mk.drawing = true;
    try { mkCanvas.setPointerCapture(e.pointerId); } catch (_) {}
    const p = mkPos(e);
    mk.last = p;
    mkPaint(p.x, p.y);
  });
  mkCanvas.addEventListener("pointermove", (e) => {
    if (!mk.drawing) return;
    e.preventDefault();
    const p = mkPos(e);
    if (mk.last) mkStroke(mk.last, p);
    mk.last = p;
  });
  const endStroke = () => {
    mk.drawing = false;
    mk.last = null;
  };
  mkCanvas.addEventListener("pointerup", endStroke);
  mkCanvas.addEventListener("pointercancel", endStroke);

  /* ---------------- 照片 ---------------- */
  const drop = $("#drop");
  const fileInput = $("#file");

  function loadViaObjectURL(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("这张图读不出来，换一张试试"));
      img.src = URL.createObjectURL(file);
    });
  }

  function loadImage(file) {
    if (window.createImageBitmap) {
      return createImageBitmap(file).catch(() => loadViaObjectURL(file));
    }
    return loadViaObjectURL(file);  // iOS 老版本没有 createImageBitmap
  }

  async function fileToDataURL(file) {
    // 小图直接把原始字节发过去：走 canvas 会重新编码，手机照片（P3）容易偏色发暗
    if (file.size <= 8 * 1024 * 1024) {
      try {
        const raw = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(r.result);
          r.onerror = () => rej(new Error("read fail"));
          r.readAsDataURL(file);
        });
        const img = await loadDataUrl(raw);
        if (Math.max(img.naturalWidth || 0, img.naturalHeight || 0) <= 2400) return raw;
      } catch (_) { /* 落到 canvas 兜底 */ }
    }
    const bmp = await loadImage(file);
    const w0 = bmp.naturalWidth || bmp.width;
    const h0 = bmp.naturalHeight || bmp.height;
    const max = 2400;
    const scale = Math.min(1, max / Math.max(w0, h0));
    const w = Math.max(1, Math.round(w0 * scale));
    const h = Math.max(1, Math.round(h0 * scale));
    const cv = document.createElement("canvas");
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext("2d", { colorSpace: "srgb" });
    ctx.drawImage(bmp, 0, 0, w, h);
    return cv.toDataURL("image/jpeg", 0.95);
  }

  function setImage(dataUrl) {
    state.image = dataUrl;
    state.cutoutPng = null;  // 换照片了，之前修好的蒙版作废
    state.placed = [];
    state.sel = -1;
    drawStickers();
    const t = $("#thumb");
    t.src = dataUrl;
    t.hidden = false;
    $("#dropHint").hidden = true;
  }

  async function handleFiles(files) {
    if (!files || !files.length) return;
    const f = files[0];
    if (!f.type.startsWith("image/")) return toast("请选择图片文件");
    try {
      setImage(await fileToDataURL(f));
      schedule();
    } catch (err) {
      toast("图片读取失败：" + err.message);
    }
  }

  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("hot");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("hot"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("hot");
    handleFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener("change", (e) => handleFiles(e.target.files));

  $("#sampleBtn").addEventListener("click", async () => {
    const res = await fetch("samples/food.jpg");
    const blob = await res.blob();
    setImage(await fileToDataURL(new File([blob], "sample.jpg", { type: "image/jpeg" })));
    $("#text").value =
      "牛肝菌沙葱牛肉饭。这道菜口感很好，味道更好，里面那个牛肝菌味道不会很夸张，不会一口下去就有蘑菇味，粒粒分明，香香润润的！";
    state.text = $("#text").value;
    schedule();
  });

  /* ---------------- 自定义字体（assets/fonts/手账体.ttf） ---------------- */
  fetch("api/styles")
    .then((r) => r.json())
    .then((d) => {
      const wrap = $("#fonts");
      const have = new Set([...wrap.querySelectorAll("button")].map((b) => b.dataset.v));
      (d.styles || []).forEach((s) => {
        if (have.has(s.key)) return;
        const b = document.createElement("button");
        b.dataset.v = s.key;
        b.textContent = s.label;
        b.addEventListener("click", () => {
          state.font = s.key;
          [...wrap.children].forEach((el) => el.classList.toggle("on", el === b));
          schedule();
        });
        wrap.appendChild(b);
      });
    })
    .catch(() => {});

  /* ---------------- 语音 ---------------- */
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  let listening = false;

  function setListening(on) {
    listening = on;
    $("#mic").classList.toggle("on", on);
    $("#micText").textContent = on ? "正在听…" : "说两句";
    if (!on) $("#interim").textContent = "";
  }

  if (!SR) {
    $("#mic").addEventListener("click", () => toast("这个浏览器不支持语音识别，用 Chrome 或 Edge 打开，或者直接打字"));
  } else {
    rec = new SR();
    rec.lang = "zh-CN";
    rec.continuous = true;
    rec.interimResults = true;

    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) {
          state.text = (state.text + r[0].transcript).trim();
          $("#text").value = state.text;
        } else {
          interim += r[0].transcript;
        }
      }
      $("#interim").textContent = interim ? "… " + interim : "";
    };
    rec.onerror = (e) => {
      setListening(false);
      const msg = {
        "not-allowed": "麦克风被拒绝，请在地址栏允许麦克风权限",
        "service-not-allowed": "语音服务不可用，请直接打字",
        "no-speech": "没听到声音，再说一次？",
        network: "语音识别需要联网，离线时请直接打字",
      }[e.error] || "语音识别出错：" + e.error;
      toast(msg);
    };
    rec.onend = () => {
      setListening(false);
      if (state.text.trim()) schedule(300);
    };

    $("#mic").addEventListener("click", () => {
      if (listening) {
        rec.stop();
        return;
      }
      try {
        rec.start();
        setListening(true);
      } catch (err) {
        toast("启动失败：" + err.message);
      }
    });
  }

  $("#text").addEventListener("input", (e) => {
    state.text = e.target.value;
    schedule();
  });

  /* ---------------- 生成 ---------------- */
  let timer = null;
  function schedule(delay = 600) {
    clearTimeout(timer);
    timer = setTimeout(generate, delay);
  }

  async function generate() {
    if (!state.image && !state.text.trim()) {
      toast("先放一张照片，或者先打两句话");
      return;
    }
    const id = ++state.reqId;
    state.busy = true;
    $("#loading").hidden = false;
    $("#go").disabled = true;
    try {
      const res = await fetch("api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image: state.image,
          text: state.text,
          palette: state.palette,
          style: state.style,
          decor: state.decor,
          ratio: state.ratio,
          layout: state.layout,
          tag_style: state.tagStyle,
          tolerance: state.tolerance,
          doodles: state.doodles,
          emoji: state.emoji,
          font: state.font,
          cutout_force: state.force,
          cutout: state.cutoutPng || "",
          seed: state.seed,
        }),
      });
      const data = await res.json();
      if (id !== state.reqId) return;
      if (!res.ok || data.error) throw new Error(data.error || res.statusText);
      applyResult(data);
    } catch (err) {
      if (id === state.reqId) toast(err.message);
    } finally {
      if (id === state.reqId) {
        state.busy = false;
        $("#loading").hidden = true;
        $("#go").disabled = false;
      }
    }
  }

  function applyResult(data) {
    state.png = data.png;
    const img = $("#preview");
    $("#pageWrap").hidden = false;
    img.onload = () => drawStickers();
    img.src = data.png;
    img.hidden = false;
    $("#placeholder").hidden = true;
    if (img.complete) drawStickers();
    $("#dl").disabled = false;

    const p = data.plan || {};
    if (p.tag || (p.intro && p.intro.length) || (p.feel && p.feel.length)) {
      $("#pTag").textContent = p.tag || "—";
      $("#pIntro").textContent = (p.intro || []).join(" / ") || "—";
      $("#pFeel").textContent = (p.feel || []).join(" / ") || "—";
      $("#parse").hidden = false;
    }
    const bits = [];
    if (p.title) bits.push(`<b>${esc(p.title)}</b>`);
    bits.push(`${(p.lines || []).length} 行长文`);
    if (data.meta && data.meta.palette) bits.push(esc(data.meta.palette.name));
    if (data.meta && data.meta.photo === "cutout") bits.push("已抠图");
    $("#meta").innerHTML = bits.join(" · ");

    if (data.warnings && data.warnings.length) toast(data.warnings[0], 5200);
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  $("#go").addEventListener("click", generate);

  async function composeWithStickers() {
    const base = await loadDataUrl(state.png);
    const W = base.naturalWidth || 1200;
    const H = base.naturalHeight || 1800;
    const cv = document.createElement("canvas");
    cv.width = W;
    cv.height = H;
    const ctx = cv.getContext("2d");
    ctx.drawImage(base, 0, 0, W, H);
    for (const s of state.placed) {
      try {
        const im = await loadDataUrl(s.url);
        const w = s.size * W;
        const h = w * (im.naturalHeight || im.height) / (im.naturalWidth || im.width);
        ctx.save();
        ctx.translate(s.x * W, s.y * H);
        ctx.rotate((s.rot || 0) * Math.PI / 180);
        ctx.drawImage(im, -w / 2, -h / 2, w, h);
        ctx.restore();
      } catch (_) {}
    }
    return await new Promise((res) => cv.toBlob(res, "image/png"));
  }

  $("#dl").addEventListener("click", async () => {
    if (!state.png) return;
    try {
      const blob = state.placed.length ? await composeWithStickers() : await (await fetch(state.png)).blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "手账-" + new Date().toISOString().slice(0, 10) + ".png";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1500);
    } catch (err) {
      toast("下载失败：" + err.message + "（图已存在 output/ 目录）");
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      generate();
    }
  });

  /* ---------------- 贴纸 ---------------- */
  fetch("api/stickers")
    .then((r) => r.json())
    .then((d) => {
      const row = $("#stickerRow");
      (d.stickers || []).forEach((s) => {
        const b = document.createElement("button");
        b.className = "stk";
        b.type = "button";
        b.dataset.v = s.name;
        b.title = s.name;
        const im = document.createElement("img");
        im.src = s.url;
        im.alt = s.name;
        b.appendChild(im);
        b.addEventListener("click", () => addSticker(s));
        row.appendChild(b);
      });
    })
    .catch(() => {});

  fetch("api/bubbles_disabled")
    .then((r) => r.json())
    .then((d) => {
      const row = $("#bubbleRow");
      if (!d.bubbles || !d.bubbles.length) return;
      $("#bubbleLabel").hidden = false;
      row.hidden = false;
      d.bubbles.forEach((s, i) => {
        const b = document.createElement("button");
        b.className = "stk" + (s.name === state.bubble ? " on" : "");
        b.type = "button";
        b.dataset.v = s.name;
        b.title = s.name;
        const im = document.createElement("img");
        im.src = s.url;
        b.appendChild(im);
        b.addEventListener("click", () => {
          state.bubble = s.name;
          [...row.children].forEach((el) => el.classList.toggle("on", el === b));
          if (state.tagStyle !== "frame") {
            state.tagStyle = "frame";
            const seg = $("#tagStyle");
            [...seg.children].forEach((el) => el.classList.toggle("on", el.dataset.v === "frame"));
          }
          schedule();
        });
        row.appendChild(b);
      });
    })
    .catch(() => {});

  /* ---------------- 贴纸层：拖动 / 缩放 / 删除 ---------------- */
  const SPOTS = [[0.14, 0.20], [0.84, 0.20], [0.14, 0.74], [0.84, 0.74], [0.5, 0.12], [0.5, 0.86]];

  function addSticker(item) {
    const i = state.placed.length;
    const spot = SPOTS[i % SPOTS.length];
    state.placed.push({
      name: item.name, url: item.url,
      x: spot[0], y: spot[1], size: 0.13, rot: -8 + Math.random() * 16,
    });
    state.sel = state.placed.length - 1;
    drawStickers();
    toast("拖到想要的位置；选中后下方滑杆改大小，右上角 × 删除");
  }

  function drawStickers() {
    const wrap = $("#pageWrap");
    const layer = $("#stkLayer");
    if (!wrap || !layer) return;
    layer.innerHTML = "";
    state.placed.forEach((s, i) => {
      const el = document.createElement("div");
      el.className = "placed" + (i === state.sel ? " sel" : "");
      el.style.left = (s.x * 100) + "%";
      el.style.top = (s.y * 100) + "%";
      el.style.width = (s.size * 100) + "%";
      applyTransform(el, s);
      const img = document.createElement("img");
      img.src = s.url;
      img.draggable = false;
      el.appendChild(img);
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "×";
      del.addEventListener("pointerdown", (e) => e.stopPropagation());
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        state.placed.splice(i, 1);
        state.sel = -1;
        drawStickers();
      });
      el.appendChild(del);
      el.addEventListener("pointerdown", (e) => startDrag(e, i, el));
      layer.appendChild(el);
    });
    $("#stkBar").hidden = state.placed.length === 0;
    const cur = state.placed[state.sel];
    if (cur) {
      $("#stkSize").value = Math.round(cur.size * 100);
      $("#stkSizeVal").textContent = Math.round(cur.size * 100);
    }
  }

  function applyTransform(el, s) {
    el.style.transform = `translate(-50%,-50%) rotate(${s.rot || 0}deg)`;
  }

  function selectSticker(i) {
    state.sel = i;
    [...$("#stkLayer").children].forEach((c, idx) => c.classList.toggle("sel", idx === i));
    const s = state.placed[i];
    if (s) {
      $("#stkSize").value = Math.round(s.size * 100);
      $("#stkSizeVal").textContent = Math.round(s.size * 100);
      $("#stkRot").value = Math.round(s.rot || 0);
      $("#stkRotVal").textContent = Math.round(s.rot || 0);
    }
  }

  function startDrag(e, i, el) {
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    selectSticker(i);
    const wrap = $("#pageWrap");
    const rect = wrap.getBoundingClientRect();
    const s = state.placed[i];
    const startX = e.clientX;
    const startY = e.clientY;
    const ox = s.x;
    const oy = s.y;
    el.classList.add("grabbing");

    // 监听挂在 window 上：元素一旦被重排也不会丢事件
    const move = (ev) => {
      s.x = Math.min(1.05, Math.max(-0.05, ox + (ev.clientX - startX) / rect.width));
      s.y = Math.min(1.05, Math.max(-0.05, oy + (ev.clientY - startY) / rect.height));
      el.style.left = (s.x * 100) + "%";
      el.style.top = (s.y * 100) + "%";
    };
    const up = () => {
      el.classList.remove("grabbing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  $("#stkSize").addEventListener("input", (e) => {
    const s = state.placed[state.sel];
    if (!s) return;
    s.size = Number(e.target.value) / 100;
    $("#stkSizeVal").textContent = e.target.value;
    const el = [...$("#stkLayer").children][state.sel];
    if (el) el.style.width = (s.size * 100) + "%";
  });

  $("#stkRot").addEventListener("input", (e) => {
    const s = state.placed[state.sel];
    if (!s) return;
    s.rot = Number(e.target.value);
    $("#stkRotVal").textContent = e.target.value;
    const el = [...$("#stkLayer").children][state.sel];
    if (el) applyTransform(el, s);
  });

  $("#stkClear").addEventListener("click", () => {
    state.placed = [];
    state.sel = -1;
    drawStickers();
  });

  $("#stickerHint").addEventListener("click", () => {
    toast("把透明背景的 PNG 丢进 手账工坊/assets/stickers/ ，刷新页面就能选");
  });

  /* ---------------- 手机访问地址 ---------------- */
  fetch("api/info")
    .then((r) => r.json())
    .then((d) => {
      const local = ["127.0.0.1", "localhost", "::1"].includes(location.hostname);
      if (local && d.lan && d.lan.length) {
        $("#phoneUrl").textContent = `http://${d.lan[0]}:${d.port}/`;
        $("#phoneHint").hidden = false;
      }
    })
    .catch(() => {});

  /* ---------------- toast ---------------- */
  let toastTimer = null;
  function toast(msg, ms = 3200) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), ms);
  }

  syncPalette();
})();

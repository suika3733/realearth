/* ============================================================
   RealEarth — 前端逻辑 (Web 视图)
   通过 window.pywebview.api 与 Python 后端通信
   ============================================================ */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  // 注意: 页面加载时 window.pywebview 尚未注入 (before_load 在 app.js 之后触发),
  // 因此绝不能在这里缓存 api 引用, 必须在调用时动态获取。
  function api() {
    return window.pywebview ? window.pywebview.api : null;
  }

  const ACCENTS = {
    apod: ["#7C5CFC", "rgba(124,92,252,0.25)"],
    satellite: ["#00B4D8", "rgba(0,180,216,0.25)"],
    sdo: ["#FF8C00", "rgba(255,140,0,0.25)"],
  };

  let state = { source: "apod" };

  // ----------------------------------------------------------
  // 渲染
  // ----------------------------------------------------------
  function setAccent(src) {
    const [a, g] = ACCENTS[src] || ACCENTS.apod;
    document.documentElement.style.setProperty("--seg-accent", a);
    document.documentElement.style.setProperty("--seg-glow", g);
  }

  function setPreview(source, d) {
    const img = $(source + "-img");
    const ph = $(source + "-placeholder");
    if (d && d.image) {
      img.src = d.image;
      img.classList.add("show");
      ph.classList.remove("show");
    } else {
      img.classList.remove("show");
      ph.classList.add("show");
    }
  }

  function setOverlay(source, d) {
    const ov = $(source + "-overlay");
    if (d && (d.image || !d.placeholder)) {
      $(source + "-title").textContent = d.title || "";
      $(source + "-meta").textContent = d.info || d.meta || "";
      ov.classList.add("show");
    } else {
      ov.classList.remove("show");
    }
  }

  function renderApod(d) {
    if (!d) return;
    setPreview("apod", d);
    setOverlay("apod", d);
    $("apod-res").textContent = d.has_image ? (d.date || "") : "";
    $("apod-page").textContent = (d.idx || 0) + " / " + (d.total || 0);
    if (d.placeholder) {
      $("apod-title").textContent = "";
      $("apod-meta").textContent = "";
    }
  }

  function renderCategories(cats) {
    const list = $("apod-cat-list");
    list.innerHTML = "";
    cats.forEach((c) => {
      const item = document.createElement("div");
      item.className = "cat-item" + (c.selected ? " active" : "");
      item.dataset.key = c.key;
      item.innerHTML =
        '<span class="cat-name">' + c.name + "</span>" +
        '<span class="cat-count">' + c.count + "</span>";
      item.onclick = () => onCatClick(c.key);
      list.appendChild(item);
    });
  }

  function renderSatellites(list, info) {
    const menu = $("sat-menu");
    menu.innerHTML = "";
    list.forEach((s) => {
      const opt = document.createElement("div");
      opt.className = "dropdown-option" + (s.selected ? " active" : "");
      opt.dataset.id = s.id;
      opt.innerHTML =
        '<span class="dd-dot" style="background:' + s.color + '"></span>' +
        "<span>" + s.name + "</span>";
      opt.onclick = () => onSatSelect(s.id);
      menu.appendChild(opt);
    });
    if (info) {
      $("sat-trigger-label").textContent = info.name;
      $("sat-info-name").textContent = info.name;
      $("sat-info-agency").textContent = info.agency;
      $("sat-info-region").textContent = info.region;
      $("sat-info-color").textContent = info.color_label;
    }
  }

  function renderSdoBands(list) {
    const bl = $("sdo-band-list");
    bl.innerHTML = "";
    list.forEach((b) => {
      const item = document.createElement("div");
      item.className = "band-item" + (b.selected ? " active" : "");
      item.dataset.key = b.key;
      item.innerHTML =
        '<span class="band-wavelength">' + (b.wavelength || b.key) + "</span>" +
        "<span>" + b.name + "</span>";
      item.onclick = () => onBandClick(b.key);
      bl.appendChild(item);
    });
  }

  function renderStatus(st) {
    if (!st) return;
    const el = $("st-mode");
    el.textContent = st.status_text || "";
    el.classList.remove("ok", "error");
    $("st-cache").textContent = "缓存: " + st.cache_count + " 张";
    $("st-disk").textContent = "磁盘占用: " + st.disk_mb + " MB";
  }

  function setStatus(text, ok) {
    const el = $("st-mode");
    el.textContent = text;
    el.classList.remove("ok", "error");
    if (ok === true) el.classList.add("ok");
    else if (ok === false) el.classList.add("error");
  }

  function setAutoUI(source, on) {
    const btn = $(source + "-auto");
    const dot = $(source + "-dot");
    btn.textContent = "自动刷新: " + (on ? "开" : "关");
    btn.classList.toggle("off", !on);
    if (on) dot.classList.add("on");
    else dot.classList.remove("on");
  }

  function renderAll(st) {
    state.source = st.source;
    setAccent(st.source);
    renderCategories(st.categories);
    renderApod(st.apod);
    renderSatellites(st.satellites, st.sat_info);
    renderSdoBands(st.sdo_bands);
    renderStatus(st.status);
    setAutoUI("sat", st.sat_auto);
    setAutoUI("sdo", st.sdo_auto);
    // 同步分段控件高亮
    syncSeg("sat-color-seg", st.sat_info && st.sat_info.color);
    syncSeg("sat-size-seg", String(st.sat_info && st.sat_info.size));
    syncSeg("set-style-seg", st.settings && st.settings.wallpaper_style);
    // 设置表单
    if (st.settings) {
      $("set-apikey").value = st.settings.api_key || "";
      setSwitch("set-auto-update", st.settings.auto_update);
      setSwitch("set-hd", st.settings.hd);
      setSwitch("set-auto-start", st.settings.auto_start);
    }
  }

  function syncSeg(id, val) {
    const seg = $(id);
    if (!seg) return;
    seg.querySelectorAll(".seg-item").forEach((it) => {
      it.classList.toggle("active", it.dataset.val === String(val));
    });
  }

  function setSwitch(id, on) {
    const sw = $(id);
    if (sw) sw.dataset.on = on ? "true" : "false";
  }

  // ----------------------------------------------------------
  // 面板切换
  // ----------------------------------------------------------
  function switchSource(source) {
    document.querySelectorAll(".nav-item").forEach((n) =>
      n.classList.toggle("active", n.dataset.source === source)
    );
    document.querySelectorAll(".panel").forEach((p) =>
      p.classList.toggle("active", p.dataset.source === source)
    );
    state.source = source;
    setAccent(source);
    if (api()) api().set_source(source).then((r) => {
      if (r && r.status) $("st-mode").textContent = r.status;
    });
  }

  // ----------------------------------------------------------
  // loading
  // ----------------------------------------------------------
  function showLoading(source) {
    const l = $(source + "-loading");
    if (l) l.classList.add("show");
  }
  function hideLoading(source) {
    const l = $(source + "-loading");
    if (l) l.classList.remove("show");
  }

  // ----------------------------------------------------------
  // 交互
  // ----------------------------------------------------------
  async function onCatClick(key) {
    const d = await api().select_category(key);
    renderApod(d);
    document.querySelectorAll("#apod-cat-list .cat-item").forEach((it) =>
      it.classList.toggle("active", it.dataset.key === key)
    );
  }

  async function onSatSelect(id) {
    const info = await api().set_satellite(id);
    renderSatellites(await api().get_satellites(), info);
    $("sat-dropdown").classList.remove("open");
  }

  async function onBandClick(key) {
    const info = await api().set_sdo_band(key);
    document.querySelectorAll("#sdo-band-list .band-item").forEach((it) =>
      it.classList.toggle("active", it.dataset.key === key)
    );
    // 选中波段后自动获取
    showLoading("sdo");
    try {
      const d = await api().fetch_sdo();
      if (d.ok) {
        setPreview("sdo", d);
        setOverlay("sdo", { title: d.title, info: d.meta, image: d.image });
        setStatus(d.status, true);
      } else {
        setStatus(d.msg || "获取失败", false);
      }
    } catch (e) {
      setStatus("网络异常，请检查连接", false);
    } finally {
      hideLoading("sdo");
    }
  }

  // ----------------------------------------------------------
  // 事件绑定
  // ----------------------------------------------------------
  function bind() {
    // 导航
    document.querySelectorAll(".nav-item").forEach((n) =>
      (n.onclick = () => switchSource(n.dataset.source))
    );

    // 窗口控制
    $("btn-min").onclick = () => api() && api().minimize();
    $("btn-max").onclick = () => api() && api().toggle_maximize();
    $("btn-close").onclick = () => openModal("close-modal");

    // 侧边栏
    $("btn-settings").onclick = () => openModal("settings-modal");
    $("btn-help").onclick = () => openModal("help-modal");

    // APOD
    $("apod-prev").onclick = async () => renderApod(await api().prev_image());
    $("apod-next").onclick = async () => renderApod(await api().next_image());
    $("apod-set").onclick = () => doWallpaper("set_apod_wallpaper");
    $("apod-update").onclick = async () => {
      showLoading("apod");
      try {
        const d = await api().update_now();
        if (d.ok) {
          renderApod(d.apod);
          renderCategories(d.categories);
        } else {
          setStatus(d.msg || "更新失败", false);
        }
      } catch (e) {
        setStatus("网络异常，请检查连接", false);
      } finally {
        hideLoading("apod");
      }
    };
    $("btn-fetch-history").onclick = async () => {
      const days = window.prompt("获取最近多少天的 APOD 图片？(1-365)", "10");
      if (!days) return;
      const n = parseInt(days, 10) || 10;
      showLoading("apod");
      try {
        const d = await api().fetch_apod(n);
        if (d.ok) {
          renderApod(d.apod);
          renderCategories(d.categories);
          setStatus("已获取 " + d.count + " 张图片", true);
        } else {
          setStatus(d.msg || "获取失败", false);
        }
      } catch (e) {
        setStatus("网络异常，请检查连接", false);
      } finally {
        hideLoading("apod");
      }
    };

    // 卫星下拉
    $("sat-trigger").onclick = () =>
      $("sat-dropdown").classList.toggle("open");
    // 颜色/分辨率分段
    bindSeg("sat-color-seg", (v) => api().set_sat_color(v));
    bindSeg("sat-size-seg", (v) => api().set_sat_size(v));
    $("sat-fetch").onclick = async () => {
      showLoading("sat");
      try {
        const d = await api().fetch_satellite();
        if (d.ok) {
          setPreview("sat", d);
          setOverlay("sat", { title: d.title, info: d.meta, image: d.image });
          setStatus(d.status, true);
        } else {
          setStatus(d.msg || "获取失败", false);
        }
      } catch (e) {
        setStatus("网络异常，请检查连接", false);
      } finally {
        hideLoading("sat");
      }
    };
    $("sat-set").onclick = () => doWallpaper("set_sat_wallpaper");
    $("sat-auto").onclick = async () => {
      const r = await api().toggle_sat_auto_refresh();
      setAutoUI("sat", r.on);
    };

    // SDO
    $("sdo-fetch").onclick = async () => {
      showLoading("sdo");
      try {
        const d = await api().fetch_sdo();
        if (d.ok) {
          setPreview("sdo", d);
          setOverlay("sdo", { title: d.title, info: d.meta, image: d.image });
          setStatus(d.status, true);
        } else {
          setStatus(d.msg || "获取失败", false);
        }
      } catch (e) {
        setStatus("网络异常，请检查连接", false);
      } finally {
        hideLoading("sdo");
      }
    };
    $("sdo-set").onclick = () => doWallpaper("set_sdo_wallpaper");
    $("sdo-auto").onclick = async () => {
      const r = await api().toggle_sdo_auto_refresh();
      setAutoUI("sdo", r.on);
    };

    // 设置
    bindSeg("set-style-seg", null);
    $("set-save").onclick = async () => {
      const style = activeVal("set-style-seg") || "fill";
      const s = {
        api_key: $("set-apikey").value,
        wallpaper_style: style,
        auto_update: $("set-auto-update").dataset.on === "true",
        hd: $("set-hd").dataset.on === "true",
        auto_start: $("set-auto-start").dataset.on === "true",
      };
      const r = await api().save_settings(s);
      if (r.ok) closeModal("settings-modal");
    };
    // 设置开关
    ["set-auto-update", "set-hd", "set-auto-start"].forEach((id) => {
      $(id).onclick = () =>
        ($(id).dataset.on = $(id).dataset.on === "true" ? "false" : "true");
    });

    // 关闭对话框
    $("close-min").onclick = async () => {
      closeModal("close-modal");
      if (api()) await api().minimize();
    };
    $("close-quit").onclick = async () => {
      closeModal("close-modal");
      if (api()) await api().quit_app();
    };

    // 模态框关闭 (X / 遮罩 / 取消按钮)
    document.querySelectorAll("[data-close]").forEach((b) => {
      b.onclick = () => b.closest(".modal-overlay").classList.remove("show");
    });
    document.querySelectorAll(".modal-overlay").forEach((ov) => {
      ov.onclick = (e) => {
        if (e.target === ov) ov.classList.remove("show");
      };
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape")
        document.querySelectorAll(".modal-overlay.show").forEach((m) =>
          m.classList.remove("show")
        );
    });

    // 关闭下拉 (点击外部)
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#sat-dropdown"))
        $("sat-dropdown").classList.remove("open");
    });
  }

  function bindSeg(id, cb) {
    const seg = $(id);
    if (!seg) return;
    seg.querySelectorAll(".seg-item").forEach((it) => {
      it.onclick = () => {
        seg.querySelectorAll(".seg-item").forEach((x) =>
          x.classList.remove("active")
        );
        it.classList.add("active");
        if (cb) cb(it.dataset.val);
      };
    });
  }

  function activeVal(id) {
    const seg = $(id);
    if (!seg) return null;
    const a = seg.querySelector(".seg-item.active");
    return a ? a.dataset.val : null;
  }

  async function doWallpaper(method) {
    const r = await api()[method]();
    if (r && r.msg) setStatus(r.msg, r.ok);
  }

  function openModal(id) {
    $(id).classList.add("show");
  }
  function closeModal(id) {
    $(id).classList.remove("show");
  }

  // ----------------------------------------------------------
  // Python 反向推送回调 (由 evaluate_js 调用)
  // ----------------------------------------------------------
  window.updateCountdown = (source, text) => {
    const t = $(source + "-cd-time");
    if (t) t.textContent = text;
    const dot = $(source + "-dot");
    if (dot) dot.classList.add("on");
  };
  window.onSatRefreshing = (b) => {
    if (b) showLoading("sat");
    else hideLoading("sat");
  };
  window.onSatRefreshed = (d) => {
    if (!d) return;
    setPreview("sat", d);
    setOverlay("sat", { title: d.title, image: d.image });
  };
  window.onSdoRefreshing = (b) => {
    if (b) showLoading("sdo");
    else hideLoading("sdo");
  };
  window.onSdoRefreshed = (d) => {
    if (!d) return;
    setPreview("sdo", d);
    setOverlay("sdo", { title: d.title, image: d.image });
  };
  window.onStatus = (text, ok) => setStatus(text, ok);
  window.onAutoFetchDone = (count) => {
    if (api()) api().get_initial_state().then(renderAll);
  };
  window.onAutoFetchFail = () => {
    setStatus("⚠ NASA API 暂时不可用，请稍后手动获取", false);
  };

  // ----------------------------------------------------------
  // 启动
  // ----------------------------------------------------------
  function start() {
    bind();
    function doInit() {
      if (!window.pywebview || !window.pywebview.api) {
        console.warn("pywebview api 不可用 (非桌面环境)");
        return;
      }
      return api().init()
        .then((st) => { renderAll(st); })
        .catch((e) => { setStatus('初始化失败: ' + (e.message || e), false); });
    }
    window.addEventListener('pywebviewready', doInit, { once: true });
    // 若事件已派发，立即补执行
    if (window.pywebview && window.pywebview.api && window.pywebview.api.init) {
      doInit();
    }
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start);
  else start();
})();

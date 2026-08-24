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
    timelapse: ["#22C55E", "rgba(34,197,94,0.25)"],
  };

  let state = { source: "apod", tl: null };

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
    // 时间流逝
    state.tlSats = st.satellites || [];
    renderTimelapse(st.timelapse);
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
      // 壁纸位置与大小
      $("set-pos-x").value = st.settings.wallpaper_pos_x ?? 50;
      $("set-pos-y").value = st.settings.wallpaper_pos_y ?? 50;
      $("set-scale").value = st.settings.wallpaper_scale ?? 50;
      updateWpPreview();
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

  // 更新壁纸位置预览框（实时反映滑块值）
  function updateWpPreview() {
    const px = parseInt($("set-pos-x").value, 10);
    const py = parseInt($("set-pos-y").value, 10);
    const sc = parseInt($("set-scale").value, 10);
    $("set-pos-x-val").textContent = px;
    $("set-pos-y-val").textContent = py;
    $("set-scale-val").textContent = sc + "%";
    const img = $("wp-preview-img");
    if (img) {
      img.style.left = px + "%";
      img.style.top = py + "%";
      img.style.transform = "translate(-50%, -50%) scale(" + sc / 50 + ")";
    }
  }

  // ----------------------------------------------------------
  // 时间流逝
  // ----------------------------------------------------------
  function tlInitState() {
    if (!state.tl) {
      state.tl = {
        sat: null, date: null, frames: [], idx: 0,
        playing: false, timer: null, thumbCache: {}, _obs: null,
        taskId: null, taskTimer: null,
      };
    }
    return state.tl;
  }

  async function renderTimelapse(ov) {
    const tl = tlInitState();
    if (!ov) return;
    renderArchiveList(ov.archive_sats || []);
    renderTlSatMenu();
    $("tl-stat-frames").textContent = (ov.total_frames || 0) + " 帧";
    $("tl-stat-mb").textContent = (ov.total_mb || 0) + " MB";
    updateLiveUI(ov.live);
    const sats = ov.sats || [];
    let preferred = tl.sat;
    if (!preferred || !sats.some((s) => s.id === preferred)) {
      const archived = (ov.archive_sats || [])[0];
      preferred = archived || (sats.length ? sats[0].id : null);
      tl.sat = preferred;
    }
    if (!preferred) {
      $("tl-day-list").innerHTML = '<div class="tl-empty">勾选「归档卫星」并获取影像后<br>这里将按天累积存档</div>';
      return;
    }
    $("tl-sat-label").textContent = satName(preferred);
    await refreshTlDays(preferred, tl.date);
  }

  function satName(id) {
    const s = (state.tlSats || []).find((x) => x.id === id);
    return s ? s.name : id;
  }

  function renderArchiveList(archived) {
    const list = $("tl-archive-list");
    list.innerHTML = "";
    (state.tlSats || []).forEach((s) => {
      const on = archived.includes(s.id);
      const item = document.createElement("div");
      item.className = "tl-archive-item" + (on ? " active" : "");
      item.dataset.id = s.id;
      item.innerHTML =
        '<span class="tl-archive-check">&#10003;</span>' +
        '<span class="tl-archive-name">' + s.name + "</span>";
      item.onclick = () => onArchiveToggle(s.id, item);
      list.appendChild(item);
    });
    if (!(state.tlSats || []).length) {
      list.innerHTML = '<div class="tl-empty">暂无卫星数据</div>';
    }
  }

  async function onArchiveToggle(id, item) {
    const on = !item.classList.contains("active");
    const r = await api().set_archive_sat(id, on);
    if (r && r.ok) {
      item.classList.toggle("active", on);
      const tl = tlInitState();
      if (on && !tl.sat) {
        tl.sat = id;
        $("tl-sat-label").textContent = satName(id);
        await refreshTlDays(id, null);
      }
    }
  }

  function renderTlSatMenu() {
    const menu = $("tl-sat-menu");
    menu.innerHTML = "";
    (state.tlSats || []).forEach((s) => {
      const opt = document.createElement("div");
      opt.className = "dropdown-option";
      opt.dataset.id = s.id;
      opt.innerHTML =
        '<span class="dd-dot" style="background:' + s.color + '"></span>' +
        "<span>" + s.name + "</span>";
      opt.onclick = () => onTlSatSelect(s.id);
      menu.appendChild(opt);
    });
  }

  async function onTlSatSelect(id) {
    const tl = tlInitState();
    tl.sat = id;
    $("tl-sat-label").textContent = satName(id);
    $("tl-sat-dropdown").classList.remove("open");
    tlStopPreview();
    await refreshTlDays(id, null);
  }

  async function refreshTlDays(sat, keepDate) {
    const tl = tlInitState();
    const list = $("tl-day-list");
    list.innerHTML = '<div class="tl-empty">加载日期…</div>';
    try {
      const d = await api().get_timelapse_days(sat);
      const days = (d.days || []).reverse(); // 最新在前
      if (!days.length) {
        list.innerHTML = '<div class="tl-empty">暂无归档<br>勾选归档卫星后自动累积<br>或使用「历史回填」补数据</div>';
        $("tl-thumb-strip").innerHTML = "";
        clearTlPreview();
        return;
      }
      list.innerHTML = "";
      let selected = days[0].date;
      if (keepDate && days.some((dd) => dd.date === keepDate)) selected = keepDate;
      days.forEach((dd) => {
        const item = document.createElement("div");
        item.className = "tl-day-item";
        item.dataset.date = dd.date;
        item.innerHTML =
          dd.date +
          '<span class="tl-day-meta">' + dd.frames + " 帧 · " + dd.size_mb + " MB</span>";
        item.onclick = () => onTlDaySelect(sat, dd.date);
        item.classList.toggle("active", dd.date === selected);
        list.appendChild(item);
      });
      tl.date = selected;
      await loadFrames(sat, selected);
    } catch (e) {
      list.innerHTML = '<div class="tl-empty">日期加载失败</div>';
    }
  }

  async function onTlDaySelect(sat, date) {
    const tl = tlInitState();
    tl.date = date;
    document.querySelectorAll("#tl-day-list .tl-day-item").forEach((it) =>
      it.classList.toggle("active", it.dataset.date === date)
    );
    tlStopPreview();
    await loadFrames(sat, date);
  }

  async function loadFrames(sat, date) {
    const tl = tlInitState();
    tlStopPreview();
    tl.thumbCache = {};
    showLoading("tl");
    try {
      const d = await api().get_timelapse_frames(sat, date);
      tl.frames = d.frames || [];
      tl.idx = 0;
      renderThumbStrip();
      if (tl.frames.length) {
        $("tl-play-ctl").classList.add("show");
        await showFrame(tl.frames[0].time);
        updatePlayInfo();
      } else {
        clearTlPreview();
      }
    } catch (e) {
      setStatus("帧序列加载失败", false);
    } finally {
      hideLoading("tl");
    }
  }

  function renderThumbStrip() {
    const tl = tlInitState();
    const strip = $("tl-thumb-strip");
    strip.innerHTML = "";
    if (!tl.frames.length) return;
    tl.frames.forEach((f, i) => {
      const img = document.createElement("img");
      img.className = "tl-thumb" + (i === tl.idx ? " active" : "");
      img.dataset.idx = i;
      img.dataset.time = f.time;
      img.title = fmtTime(f.time);
      img.onclick = () => onThumbClick(i);
      strip.appendChild(img);
    });
    if (!tl._obs) {
      tl._obs = new IntersectionObserver((entries) => {
        entries.forEach((en) => {
          if (en.isIntersecting) loadThumb(en.target);
        });
      }, { root: $("tl-thumb-strip"), rootMargin: "200px" });
    }
    strip.querySelectorAll(".tl-thumb").forEach((el) => tl._obs.observe(el));
  }

  function loadThumb(img) {
    const tl = tlInitState();
    const t = img.dataset.time;
    if (img.src || tl.thumbCache[t]) return;
    tl.thumbCache[t] = true;
    api()
      .get_timelapse_frame_image(tl.sat, tl.date, t)
      .then((d) => {
        if (d && d.ok) img.src = d.image;
      })
      .catch(() => {});
  }

  // RAMMB time_code 是 UTC（卫星标称时刻），转本地时区显示
  function tcToLocal(tc) {
    tc = String(tc || "");
    if (tc.length < 14) return { date: "", time: tc };
    const y = +tc.slice(0, 4), mo = +tc.slice(4, 6) - 1, d = +tc.slice(6, 8);
    const h = +tc.slice(8, 10), mi = +tc.slice(10, 12), s = +tc.slice(12, 14);
    const dt = new Date(Date.UTC(y, mo, d, h, mi, s));
    const pad = (n) => String(n).padStart(2, "0");
    return {
      date: dt.getFullYear() + "-" + pad(dt.getMonth() + 1) + "-" + pad(dt.getDate()),
      time: pad(dt.getHours()) + ":" + pad(dt.getMinutes()),
    };
  }

  function fmtTime(tc) {
    return tcToLocal(tc).time;
  }

  async function showFrame(time) {
    const tl = tlInitState();
    const d = await api().get_timelapse_frame_image(tl.sat, tl.date, time);
    if (!d || !d.ok) return;
    const loc = tcToLocal(time);
    $("tl-frame-img").src = d.image;
    $("tl-frame-img").classList.add("show");
    $("tl-placeholder").classList.remove("show");
    $("tl-overlay").classList.add("show");
    $("tl-title").textContent = satName(tl.sat);
    $("tl-meta").textContent = tl.date + "  " + loc.time + " (本地)";
    $("tl-res").textContent = loc.date + " " + loc.time + " · 本地拍摄时间";
    const cur = tl.frames.findIndex((f) => f.time === time);
    if (cur >= 0) {
      tl.idx = cur;
      document.querySelectorAll(".tl-thumb").forEach((el, i) =>
        el.classList.toggle("active", i === cur)
      );
    }
  }

  function tlPlay() {
    const tl = tlInitState();
    if (!tl.frames.length) return;
    tl.playing = true;
    $("tl-play-btn").innerHTML = "&#10074;&#10074; 暂停";
    tl.timer = setInterval(async () => {
      if (!tl.playing) return;
      const n = tl.frames.length;
      if (!n) return;
      tl.idx = (tl.idx + 1) % n;
      await showFrame(tl.frames[tl.idx].time);
      updatePlayInfo();
    }, 250); // 预览 4fps（受帧 base64 传输限制）
  }

  function tlStopPreview() {
    const tl = tlInitState();
    tl.playing = false;
    if (tl.timer) {
      clearInterval(tl.timer);
      tl.timer = null;
    }
    const btn = $("tl-play-btn");
    if (btn) {
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>播放';
    }
  }

  function updatePlayInfo() {
    const tl = tlInitState();
    $("tl-play-info").textContent = (tl.idx + 1) + " / " + tl.frames.length;
  }

  function tlStep(delta) {
    const tl = tlInitState();
    if (!tl.frames.length) return;
    tlStopPreview();
    tl.idx = (tl.idx + delta + tl.frames.length) % tl.frames.length;
    document.querySelectorAll(".tl-thumb").forEach((el, j) =>
      el.classList.toggle("active", j === tl.idx)
    );
    showFrame(tl.frames[tl.idx].time);
    updatePlayInfo();
  }

  async function onThumbClick(i) {
    const tl = tlInitState();
    tlStopPreview();
    tl.idx = i;
    document.querySelectorAll(".tl-thumb").forEach((el, j) =>
      el.classList.toggle("active", j === i)
    );
    await showFrame(tl.frames[i].time);
    updatePlayInfo();
  }

  function clearTlPreview() {
    $("tl-frame-img").classList.remove("show");
    $("tl-placeholder").classList.add("show");
    $("tl-overlay").classList.remove("show");
    $("tl-play-ctl").classList.remove("show");
  }

  // ---- 动态壁纸 ----
  async function onLiveStart() {
    const tl = tlInitState();
    if (!tl.sat) {
      setStatus("请先选择卫星", false);
      return;
    }
    const d = await api().start_live_wallpaper(tl.sat, tl.date || null);
    if (d && d.ok) {
      updateLiveUI(d.live);
      setStatus("动态壁纸已启动 · 已嵌入桌面图标层之下，图标不会被遮挡 · 主窗口已最小化，点击任务栏图标可恢复控制", true);
    } else {
      setStatus((d && d.msg) || "动态壁纸启动失败", false);
    }
  }

  async function onLiveStop() {
    const d = await api().stop_live_wallpaper();
    if (d && d.ok) updateLiveUI(d.live);
  }

  async function onLivePause() {
    const d = await api().toggle_live_pause();
    if (d && d.ok) updateLiveUI(d.live);
  }

  function updateLiveUI(live) {
    const running = live && live.running;
    $("tl-live-start").style.display = running ? "none" : "";
    $("tl-live-stop").style.display = running ? "" : "none";
    $("tl-live-pause").style.display = running ? "" : "none";
    $("tl-live-text").textContent = running
      ? satName(live.sat) + " · " + (live.date || "") + " · " + (live.frames || 0) + " 帧"
      : "未运行";
    $("tl-live-dot").classList.toggle("on", running);
    $("tl-stat-live").textContent = running ? "运行中" : "未运行";
    $("tl-live-pause").textContent = running && live.paused ? "恢复" : "暂停";
  }

  // ---- 回填 / 导出 / 删除 ----
  async function onBackfill() {
    const tl = tlInitState();
    if (!tl.sat) {
      setStatus("请先选择卫星", false);
      return;
    }
    const start = $("tl-backfill-start").value;
    const end = $("tl-backfill-end").value;
    if (!start || !end || start > end) {
      setStatus("请选择正确的回填日期范围", false);
      return;
    }
    const d = await api().submit_backfill(tl.sat, start, end);
    if (d && d.ok) {
      setStatus("回填任务已提交 · 断点续传，可随时重跑", true);
      startTaskPoll(d.task_id);
    } else {
      setStatus((d && d.msg) || "回填提交失败", false);
    }
  }

  async function onExport(fmt) {
    const tl = tlInitState();
    if (!tl.sat || !tl.date) {
      setStatus("请先选择卫星与日期", false);
      return;
    }
    // 先弹出系统保存对话框选择导出路径
    const ext = fmt === "mp4" ? "mp4" : "gif";
    const dlg = await api().choose_export_path(
      tl.sat + "_" + tl.date + "." + ext, fmt);
    if (!dlg) return;
    if (!dlg.ok) {
      if (!dlg.canceled) setStatus(dlg.msg || "导出取消", false);
      return;
    }
    const d = await api().submit_export(tl.sat, tl.date, tl.date, fmt, 10, 1, dlg.path);
    if (d && d.ok) {
      setStatus(fmt.toUpperCase() + " 导出任务已提交 · 保存至所选路径", true);
      startTaskPoll(d.task_id);
    } else {
      setStatus((d && d.msg) || "导出提交失败", false);
    }
  }

  async function onDeleteDay() {
    const tl = tlInitState();
    if (!tl.sat || !tl.date) {
      setStatus("请先选择日期", false);
      return;
    }
    if (!window.confirm("确认删除 " + tl.date + " 的全部 " + tl.frames.length + " 帧？此操作不可恢复。"))
      return;
    const d = await api().delete_timelapse_days(tl.sat, [tl.date]);
    if (d && d.ok) {
      setStatus("已删除 " + d.deleted + " 帧", true);
      const ov = await api().get_timelapse_overview();
      renderTimelapse(ov);
    }
  }

  // ---- 任务进度轮询 ----
  function startTaskPoll(tid) {
    const tl = tlInitState();
    if (tl.taskTimer) clearInterval(tl.taskTimer);
    tl.taskId = tid;
    $("tl-task-box").style.display = "";
    $("tl-task-fill").style.width = "0%";
    $("tl-task-msg").textContent = "提交中…";
    tl.taskTimer = setInterval(async () => {
      const p = await api().get_task_progress(tid);
      if (!p || !p.found) {
        stopTaskPoll("任务不存在");
        return;
      }
      $("tl-task-fill").style.width = (p.pct || 0) + "%";
      $("tl-task-msg").textContent = p.msg || p.status;
      if (p.running) return;
      const tl2 = tlInitState();
      const done = p.status === "done";
      const msg = p.msg || (done ? "任务完成" : "任务已结束");
      stopTaskPoll(msg);
      if (done) {
        setStatus("任务完成: " + msg, true);
        if (tl2.sat) refreshTlDays(tl2.sat, tl2.date);
      } else if (p.status === "error") {
        setStatus("任务失败: " + (p.error || ""), false);
      }
    }, 1000);
  }

  function stopTaskPoll(msg) {
    const tl = tlInitState();
    if (tl.taskTimer) {
      clearInterval(tl.taskTimer);
      tl.taskTimer = null;
    }
    $("tl-task-box").style.display = "none";
    if (msg) $("tl-task-msg").textContent = msg;
  }

  async function onTaskCancel() {
    const tl = tlInitState();
    if (tl.taskId) await api().cancel_task(tl.taskId);
    setStatus("已请求取消任务", false);
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

    // 时间流逝
    $("tl-sat-trigger").onclick = () =>
      $("tl-sat-dropdown").classList.toggle("open");
    $("tl-play-btn").onclick = () =>
      tlInitState().playing ? tlStopPreview() : tlPlay();
    $("tl-play-prev").onclick = () => tlStep(-1);
    $("tl-play-next").onclick = () => tlStep(1);
    $("tl-live-start").onclick = onLiveStart;
    $("tl-live-stop").onclick = onLiveStop;
    $("tl-live-pause").onclick = onLivePause;
    $("tl-backfill-btn").onclick = onBackfill;
    $("tl-export-gif").onclick = () => onExport("gif");
    $("tl-export-mp4").onclick = () => onExport("mp4");
    $("tl-delete-day").onclick = onDeleteDay;
    $("tl-task-cancel").onclick = onTaskCancel;
    // 回填日期默认值: 今天 与 前天
    {
      const today = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      const fmt = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
      const ago = new Date(today);
      ago.setDate(today.getDate() - 2);
      $("tl-backfill-end").value = fmt(today);
      $("tl-backfill-start").value = fmt(ago);
    }

    // 设置
    bindSeg("set-style-seg", null);
    $("set-save").onclick = async () => {
      const style = activeVal("set-style-seg") || "fill";
      const s = {
        api_key: $("set-apikey").value,
        wallpaper_style: style,
        wallpaper_pos_x: parseInt($("set-pos-x").value, 10),
        wallpaper_pos_y: parseInt($("set-pos-y").value, 10),
        wallpaper_scale: parseInt($("set-scale").value, 10),
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
    // 壁纸位置滑块实时预览
    ["set-pos-x", "set-pos-y", "set-scale"].forEach((id) => {
      $(id).oninput = updateWpPreview;
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
      if (!e.target.closest("#tl-sat-dropdown"))
        $("tl-sat-dropdown").classList.remove("open");
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

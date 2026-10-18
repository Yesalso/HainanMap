/* ==========================================================
   海南省行政区划地图 · 应用逻辑
   依赖：data.js（window.HN_DATA，含乡镇边界与人口普查数据）
   模块划分（按函数用途）：
     1. 数据与常量     —— 调色板、分箱、人口格式化
     2. 视图与缩放     —— 坐标适配、平移缩放、定位
     3. 地图渲染       —— SVG 路径绘制、着色、标注、图例
     4. 提示气泡       —— 悬浮信息
     5. 选中与详情     —— 详情面板填充、排名
     6. 模式与范围切换 —— 行政 / 面积 / 人口，市县 / 乡镇
     7. 搜索           —— 建议下拉与跳转
     8. 启动           —— 事件绑定与初始化
   ========================================================== */
(function () {
  'use strict';

  /* ======================================================
   * 1. 数据与常量
   * ====================================================== */
  var D = window.HN_DATA;
  var TOWNS = D.towns, CITIES = D.cities;
  var NS = 'http://www.w3.org/2000/svg';

  // 行政模式：五色着色（相邻市县不同色，结果存于数据 ci 字段）
  // 五色相拉开：紫 270° / 粉 330° / 橙 35° / 青 195° / 黄绿 100°，饱和度略提高以增强区分度
  var PALETTE = ['#c9b3e8', '#f7a6c1', '#fac388', '#8ed4ee', '#aed994'];
  var cityCi = {};
  CITIES.forEach(function (c) { cityCi[c.name] = c.ci; });

  // 面积模式：蓝色渐变（线性分箱）
  var AREA_COLORS = ['#e8f1fb', '#c3dcf5', '#8fbce9', '#5596d8', '#2b6cb8', '#123c7a'];

  // 人口模式：红橙渐变（分位数分箱，人口分布右偏，用分位数更均匀）
  var POP_COLORS = ['#fdeade', '#f9d0b4', '#f2ab7e', '#e5834f', '#c2542a', '#8a3113'];

  // 可选普查（key 对应数据字段 pop7/pop6/pop5）
  var POP_CENSUSES = {
    pop7: { label: '七普', year: 2020 },
    pop6: { label: '六普', year: 2010 },
    pop5: { label: '五普', year: 2000 }
  };

  // 乡镇面积分箱（分位数）
  var areas = TOWNS.map(function (t) { return t.area || 0; })
    .sort(function (a, b) { return a - b; });

  function quantile(arr, q) {
    var i = (arr.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
    return arr[lo] + (arr[hi] - arr[lo]) * (i - lo);
  }

  // 线性分箱着色（面积模式）
  function binnedColor(v, list) {
    var lo = list[0], hi = list[list.length - 1];
    var t = (v - lo) / ((hi - lo) || 1);
    var idx = 0;
    for (var i = 1; i <= 5; i++) { if (t >= (i - 1) / 5) idx = i; }
    return AREA_COLORS[idx];
  }

  // 分位数分箱着色（人口模式）：thresholds 为 5 个分位断点
  function quantBinnedColor(v, thresholds) {
    var idx = 0;
    for (var i = 0; i < thresholds.length; i++) { if (v >= thresholds[i]) idx = i + 1; }
    return POP_COLORS[idx];
  }

  // 人口格式化：精确值，≥1 万写成"3万2768"，<1 万直接写数值（如 9900）
  function fmtPop(n) {
    n = Math.round(n || 0);
    if (n < 10000) return String(n);
    var w = Math.floor(n / 10000), r = n - w * 10000;
    return r === 0 ? w + '万' : w + '万' + r;
  }

  /* ======================================================
   *  全局状态
   * ====================================================== */
  var mode = 'admin';   // admin | area | pop
  var scope = 'city';   // city | town
  var popKey = 'pop7';  // 人口模式当前普查：pop7 | pop6 | pop5
  // 支持 URL 参数：?scope=town&mode=area&census=pop6
  try {
    var qs = new URLSearchParams(location.search);
    if (qs.get('scope') === 'town') scope = 'town';
    if (qs.get('mode') === 'area') mode = 'area';
    if (qs.get('mode') === 'pop') mode = 'pop';
    if (POP_CENSUSES[qs.get('census')]) popKey = qs.get('census');
  } catch (e) {}
  var selected = null;  // {type:'town'|'city', item:...}

  // 视图（数据已预投影为像素坐标，y 向下）
  var bb = { x0: 0, y0: 0, x1: D.viewW, y1: D.viewH };
  var W = 0, H = 0;
  var mapArea = document.getElementById('mapArea');
  var svg = document.getElementById('svg');
  var BASE_K = 1;
  var view = { k: 1, tx: 0, ty: 0, userMoved: false };
  var gRoot = null, soEl = null;

  function $(id) { return document.getElementById(id); }

  /* ======================================================
   * 2. 视图与缩放
   * ====================================================== */
  function resize() {
    W = mapArea.clientWidth; H = mapArea.clientHeight;
    svg.setAttribute('width', W); svg.setAttribute('height', H);
    BASE_K = Math.min(W / (bb.x1 - bb.x0), H / (bb.y1 - bb.y0)) * 0.94;
    if (!view.userMoved) resetView();
    apply();
  }
  function resetView() {
    view.k = 1;
    view.tx = W / 2 - (bb.x0 + bb.x1) / 2 * BASE_K;
    view.ty = H / 2 - (bb.y0 + bb.y1) / 2 * BASE_K;
    view.userMoved = false;
  }
  function apply() {
    var k = BASE_K * view.k;
    if (gRoot) gRoot.setAttribute('transform', 'translate(' + view.tx + ',' + view.ty + ') scale(' + k + ')');
    var inv = 1 / k;
    svg.querySelectorAll('path.region').forEach(function (p) {
      p.setAttribute('stroke-width', (parseFloat(p.dataset.w) || 1) * inv);
    });
    svg.querySelectorAll('text.rlabel').forEach(function (t) {
      var fs = parseFloat(t.dataset.fs);
      t.setAttribute('font-size', fs * inv);
      t.setAttribute('stroke-width', 3 * inv);
      if (t.dataset.kind === 'town') {
        t.style.display = (view.k >= 2.2 && scope === 'town') ? '' : 'none';
      }
    });
    if (soEl) soEl.setAttribute('stroke-width', 2.5 * inv);
  }

  function bboxOf(it) {
    var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    it.polys.forEach(function (p) { p[0][0].forEach(function (pt) {
      var x = pt[0], y = pt[1];
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }); });
    return { x0: x0, y0: y0, x1: x1, y1: y1 };
  }

  function zoomTo(it) {
    var b = bboxOf(it);
    var cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
    var k = Math.min(W / ((b.x1 - b.x0) * BASE_K), H / ((b.y1 - b.y0) * BASE_K)) * 0.8;
    k = Math.max(1, Math.min(k, 40));
    view.k = k; view.userMoved = true;
    view.tx = W / 2 - cx * BASE_K * k;
    view.ty = H / 2 - cy * BASE_K * k;
    apply();
  }

  // ---------- 鼠标平移 / 滚轮缩放 / 控件缩放 ----------
  var dragging = false, dx0 = 0, dy0 = 0, tx0 = 0, ty0 = 0, justDragged = false;
  mapArea.addEventListener('mousedown', function (e) {
    dragging = true; justDragged = false; dx0 = e.clientX; dy0 = e.clientY; tx0 = view.tx; ty0 = view.ty;
    mapArea.classList.add('dragging');
  });
  window.addEventListener('mousemove', function (e) {
    if (!dragging) return;
    var dx = e.clientX - dx0, dy = e.clientY - dy0;
    if (Math.abs(dx) + Math.abs(dy) > 3) { justDragged = true; view.userMoved = true; }
    view.tx = tx0 + dx; view.ty = ty0 + dy; apply();
  });
  window.addEventListener('mouseup', function () { dragging = false; mapArea.classList.remove('dragging'); });
  mapArea.addEventListener('wheel', function (e) {
    e.preventDefault();
    var r = mapArea.getBoundingClientRect();
    var px = e.clientX - r.left, py = e.clientY - r.top;
    var f = e.deltaY < 0 ? 1.25 : 0.8;
    zoomAt(px, py, f);
  }, { passive: false });
  $('zin').onclick = function () { zoomCenter(1.3); };
  $('zout').onclick = function () { zoomCenter(1 / 1.3); };
  $('zreset').onclick = function () { resetView(); apply(); };
  function zoomAt(px, py, f) {
    var nk = Math.max(1, Math.min(60, view.k * f));
    view.tx = px - (px - view.tx) * (nk / view.k);
    view.ty = py - (py - view.ty) * (nk / view.k);
    view.k = nk; view.userMoved = true;
    apply();
  }
  function zoomCenter(f) {
    var nk = Math.max(1, Math.min(60, view.k * f));
    view.tx = W / 2 - (W / 2 - view.tx) * (nk / view.k);
    view.ty = H / 2 - (H / 2 - view.ty) * (nk / view.k);
    view.k = nk; view.userMoved = true; apply();
  }

  /* ======================================================
   * 2.5 点击空白海域取消选中
   * ====================================================== */
  // 点击海南岛外围外扩 CLEAR_MARGIN 像素范围内的空白（海面）时，
  // 清除选中状态、关闭详情面板，恢复到页面初始状态；
  // 更远处（地图角落）的空白点击不产生任何效果。
  var CLEAR_MARGIN = 80;
  svg.addEventListener('click', function (e) {
    // 点在行政区划面上：由路径自身的 click 逻辑处理（选中/忽略拖拽）
    if (e.target && e.target.closest && e.target.closest('path.region')) return;
    // 拖拽平移结束后浏览器仍会触发 click，不能当作"点击海面"
    if (justDragged) { justDragged = false; return; }

    // 计算点击位置（屏幕坐标）与海南岛外扩范围的屏幕包围盒
    var r = svg.getBoundingClientRect();
    var px = e.clientX - r.left, py = e.clientY - r.top;
    var k = BASE_K * view.k;
    var x0 = bb.x0 * k + view.tx - CLEAR_MARGIN;
    var y0 = bb.y0 * k + view.ty - CLEAR_MARGIN;
    var x1 = bb.x1 * k + view.tx + CLEAR_MARGIN;
    var y1 = bb.y1 * k + view.ty + CLEAR_MARGIN;
    if (px < x0 || px > x1 || py < y0 || py > y1) return;

    // 与右上角 ✕ 按钮同一套清除逻辑
    $('dClose').onclick();
  });

  /* ======================================================
   * 3. 地图渲染
   * ====================================================== */
  function pathD(polys) {
    var d = '';
    polys.forEach(function (poly) {
      poly.forEach(function (rings) {
        rings.forEach(function (ring) {
          d += 'M' + ring.map(function (p) { return p[0].toFixed(2) + ',' + p[1].toFixed(2); }).join('L') + 'Z';
        });
      });
    });
    return d;
  }

  // 按当前模式计算填充色；同时返回图例所需的取值列表
  function fillColorOf(it, metricList) {
    if (mode === 'admin') {
      return PALETTE[scope === 'city' ? it.ci : cityCi[it.city]];
    }
    if (mode === 'area') {
      return binnedColor(it.area || 0, metricList);
    }
    return quantBinnedColor(it[popKey] || 0, metricList.thresholds);
  }

  // 当前模式的取值列表（面积：排序数组；人口：分位断点对象）
  function metricOfScope() {
    if (scope === 'city') {
      return mode === 'pop'
        ? { thresholds: quantThresholds(CITIES.map(function (c) { return c[popKey] || 0; })) }
        : CITIES.map(function (c) { return c.area; }).sort(function (a, b) { return a - b; });
    }
    return mode === 'pop'
      ? { thresholds: quantThresholds(TOWNS.map(function (t) { return t[popKey] || 0; })) }
      : areas.slice();
  }
  function quantThresholds(arr) {
    var s = arr.slice().sort(function (a, b) { return a - b; });
    var th = [];
    for (var i = 1; i <= 5; i++) th.push(quantile(s, i / 6));
    return th;
  }

  function render() {
    svg.innerHTML = '';
    hideTip();
    soEl = null;
    gRoot = document.createElementNS(NS, 'g');
    gRoot.id = 'vp';
    svg.appendChild(gRoot);

    var items = scope === 'city' ? CITIES : TOWNS;
    var metric = metricOfScope();

    items.forEach(function (it) {
      var p = document.createElementNS(NS, 'path');
      p.setAttribute('d', pathD(it.polys));
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('class', 'region');
      p.setAttribute('stroke', '#fff');
      p.dataset.w = scope === 'city' ? 1.5 : 1;
      p.setAttribute('fill', fillColorOf(it, metric));
      p.addEventListener('mouseenter', function (e) { showTip(it, e); });
      p.addEventListener('mousemove', moveTip);
      p.addEventListener('mouseleave', hideTip);
      p.addEventListener('click', function () { if (!justDragged) selectItem(it, scope === 'city' ? 'city' : 'town', true); });
      gRoot.appendChild(p);
    });

    // 名称标注
    items.forEach(function (it) {
      var t = document.createElementNS(NS, 'text');
      t.setAttribute('class', 'rlabel ' + (scope === 'city' ? 'city' : 'town'));
      t.setAttribute('x', it.lp[0].toFixed(2));
      t.setAttribute('y', it.lp[1].toFixed(2));
      t.dataset.fs = scope === 'city' ? 13 : 10;
      t.dataset.kind = scope === 'city' ? 'city' : 'town';
      t.textContent = scope === 'city'
        ? it.name.replace('黎族苗族自治县', '自治县').replace('黎族自治县', '自治县')
        : it.town;
      gRoot.appendChild(t);
    });

    renderLegend(metric);
    apply();
  }

  function renderLegend(metric) {
    var lg = $('legend');
    if (mode === 'admin') { lg.style.display = 'none'; return; }
    lg.style.display = 'block';

    var colors = mode === 'pop' ? POP_COLORS : AREA_COLORS;
    var lo, hi, title;
    if (mode === 'area') {
      title = (scope === 'city' ? '市县' : '乡镇') + '面积（km²）';
      lo = metric[0]; hi = metric[metric.length - 1];
    } else {
      title = (scope === 'city' ? '市县' : '乡镇') + '常住人口（' +
        POP_CENSUSES[popKey].label + '）';
      var arr = (scope === 'city' ? CITIES.map(function (c) { return c[popKey] || 0; })
                                  : TOWNS.map(function (t) { return t[popKey] || 0; }))
        .sort(function (a, b) { return a - b; });
      lo = arr[0]; hi = arr[arr.length - 1];
    }
    $('lgTitle').textContent = title;
    var cells = $('lgCells'), labs = $('lgLabs');
    cells.innerHTML = ''; labs.innerHTML = '';
    colors.forEach(function (c) {
      var d = document.createElement('div'); d.className = 'cell'; d.style.background = c; cells.appendChild(d);
    });
    var marks = mode === 'pop' ? [0, 0.5, 1] : [0, 0.5, 1];
    marks.forEach(function (q) {
      var v = lo + (hi - lo) * q;
      var s = document.createElement('span');
      s.textContent = mode === 'pop' ? fmtPop(Math.round(v)) : Math.round(v);
      labs.appendChild(s);
    });
  }

  /* ======================================================
   * 4. 提示气泡
   * ====================================================== */
  var tip = $('tip');
  function showTip(it, e) {
    var pop = fmtPop(it[popKey] || 0) + ' 人';
    tip.textContent = scope === 'city'
      ? it.name + ' · ' + it.towns + '个乡镇 · ' + pop + ' · ' + Math.round(it.area) + ' km²'
      : it.town + ' · ' + pop + ' · ' + Math.round(it.area) + ' km²';
    tip.style.display = 'block'; moveTip(e);
  }
  function moveTip(e) {
    var r = mapArea.getBoundingClientRect();
    tip.style.left = (e.clientX - r.left + 14) + 'px';
    tip.style.top = (e.clientY - r.top + 14) + 'px';
  }
  function hideTip() { tip.style.display = 'none'; }

  /* ======================================================
   * 5. 选中与详情面板
   * ====================================================== */
  var detail = $('detail');

  // 排名信息：key 为 'area' 或 'pop7'
  function rankInfo(it, key) {
    var pool = scope === 'town' ? TOWNS.filter(function (x) { return x.city === it.city; })
                                : CITIES.slice();
    pool.sort(function (a, b) { return (b[key] || 0) - (a[key] || 0); });
    return { rank: pool.indexOf(it) + 1, total: pool.length,
             max: pool.length ? pool[0][key] || 0 : 1,
             scopeName: scope === 'city' ? '全省市县' : it.city };
  }

  function selectItem(it, type, open) {
    selected = { type: type, item: it };
    if (soEl) soEl.remove();
    soEl = document.createElementNS(NS, 'path');
    soEl.id = 'selOutline';
    soEl.setAttribute('d', pathD(it.polys));
    soEl.setAttribute('fill', 'none');
    soEl.setAttribute('stroke', '#111');
    gRoot.appendChild(soEl);
    apply();

    var area = it.area || 0;
    var pop = it[popKey] || 0;
    var rkArea = rankInfo(it, 'area');
    var rkPop = rankInfo(it, popKey);
    var cur = POP_CENSUSES[popKey];

    $('dCrumb').textContent = type === 'city' ? '海南省 · 市县' : '海南省 · ' + it.city;
    $('dName').textContent = type === 'city' ? it.name : it.town;
    $('dArea').textContent = area.toFixed(1) + ' km²';
    $('dPopK').textContent = '常住人口（' + cur.label + ' ' + cur.year + '）';
    $('dPop').textContent = fmtPop(pop) + ' 人';

    $('dCityRow').style.display = type === 'town' ? '' : 'none';
    if (type === 'town') $('dCity').textContent = it.city;
    $('dCodeRow').style.display = type === 'town' ? '' : 'none';
    if (type === 'town') $('dCode').textContent = it.code || '-';

    // 下辖乡镇构成（仅市县）：按 区/市/镇/乡 后缀分类计数，没有的类别不显示
    if (type === 'city') {
      var cnt = {};
      TOWNS.forEach(function (t) {
        if (t.city !== it.name) return;
        var n = t.town, k = null;
        if (/区$/.test(n)) k = '区';
        else if (/市$/.test(n)) k = '市';
        else if (/镇$/.test(n)) k = '镇';
        else if (/乡$/.test(n)) k = '乡';
        if (k) cnt[k] = (cnt[k] || 0) + 1;
      });
      var parts = ['区', '市', '镇', '乡'].filter(function (k) { return cnt[k]; })
        .map(function (k) { return cnt[k] + ' 个' + k; });
      $('dCompRow').style.display = parts.length ? '' : 'none';
      $('dComp').textContent = parts.join(' · ');
    } else {
      $('dCompRow').style.display = 'none';
    }

    // 人口排名（按当前选中普查）
    $('dPopRankK').textContent = '人口排名（' + rkPop.scopeName + '，' + cur.label + '）';
    $('dPopRankV').textContent = '第 ' + rkPop.rank + ' 位 / 共 ' + rkPop.total + ' 个';
    $('dPopRankBar').style.width = Math.min(100, pop / (rkPop.max || 1) * 100) + '%';

    // 历次普查：其余普查数值 + 相对上一次普查的增长率
    var prevKey = popKey === 'pop7' ? 'pop6' : popKey === 'pop6' ? 'pop5' : null;
    var hist = '';
    if (prevKey && it[prevKey] != null && it[prevKey] > 0) {
      var g = (pop - it[prevKey]) / it[prevKey] * 100;
      hist = '较' + POP_CENSUSES[prevKey].label + '增长 ' + (g >= 0 ? '+' : '') + g.toFixed(1) + '%';
    }
    if (popKey !== 'pop6' && it.pop6 != null) {
      hist += (hist ? '<br>' : '') + '六普（2010）' + fmtPop(it.pop6) + ' 人';
    }
    if (popKey !== 'pop5' && it.pop5 != null) {
      hist += (hist ? '<br>' : '') + '五普（2000）' + fmtPop(it.pop5) + ' 人';
    }
    $('dPopHistRow').style.display = '';
    $('dPopHist').innerHTML = hist || '-';

    // 面积排名
    $('dRankK').textContent = '面积排名（' + rkArea.scopeName + '）';
    $('dRankV').textContent = '第 ' + rkArea.rank + ' 位 / 共 ' + rkArea.total + ' 个';
    $('dBar').style.width = Math.min(100, area / (rkArea.max || 1) * 100) + '%';

    var btn = $('dGoTown');
    if (type === 'city') {
      btn.style.display = '';
      btn.textContent = '查看下属乡镇分布 →';
      btn.onclick = function () {
        setScope('town');
        var city = CITIES.filter(function (c) { return c.name === it.name; })[0];
        if (city) { zoomTo(city); }
        detail.classList.add('open');
      };
    } else {
      btn.style.display = 'none';
    }
    if (open) detail.classList.add('open');
  }

  $('dClose').onclick = function () {
    detail.classList.remove('open');
    if (soEl) { soEl.remove(); soEl = null; }
    selected = null;
  };
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') $('dClose').onclick();
  });

  /* ======================================================
   * 6. 模式 / 范围切换
   * ====================================================== */
  function setMode(m) {
    mode = m;
    $('tabAdmin').classList.toggle('active', m === 'admin');
    $('tabArea').classList.toggle('active', m === 'area');
    $('tabPop').classList.toggle('active', m === 'pop');
    // 普查选择器仅在人口分布模式下显示
    $('censusLabel').style.display = m === 'pop' ? '' : 'none';
    $('censusTabs').style.display = m === 'pop' ? '' : 'none';
    render();
    if (selected) selectItem(selected.item, selected.type, true);
  }
  function setCensus(k) {
    if (!POP_CENSUSES[k]) return;
    popKey = k;
    ['pop7', 'pop6', 'pop5'].forEach(function (key) {
      $('cen' + key.charAt(3)).classList.toggle('active', key === popKey);
    });
    render();
    if (selected) selectItem(selected.item, selected.type, true);
  }
  $('cen7').onclick = function () { setCensus('pop7'); };
  $('cen6').onclick = function () { setCensus('pop6'); };
  $('cen5').onclick = function () { setCensus('pop5'); };
  function setScope(s) {
    scope = s;
    selected = null; if (soEl) { soEl.remove(); soEl = null; }
    detail.classList.remove('open');
    $('scCity').classList.toggle('active', s === 'city');
    $('scTown').classList.toggle('active', s === 'town');
    resetView();
    render();
  }
  $('tabAdmin').onclick = function () { setMode('admin'); };
  $('tabArea').onclick = function () { setMode('area'); };
  $('tabPop').onclick = function () { setMode('pop'); };
  $('scCity').onclick = function () { setScope('city'); };
  $('scTown').onclick = function () { setScope('town'); };

  // 地名显示开关（默认显示；取消勾选则隐藏地图上全部标注）
  $('chkLabels').onchange = function () {
    mapArea.classList.toggle('nolabels', !this.checked);
  };

  /* ======================================================
   * 7. 搜索（建议下拉）
   * ====================================================== */
  var input = $('search');
  var sg = $('suggest');
  var matches = [], hlIdx = -1;

  input.addEventListener('input', doSearch);
  input.addEventListener('focus', doSearch);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!matches.length) return;
      hlIdx = (hlIdx + (e.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length;
      paintHl();
    } else if (e.key === 'Enter') {
      if (matches.length) pick(Math.max(0, hlIdx));
    } else if (e.key === 'Escape') {
      sg.style.display = 'none';
    }
  });
  document.addEventListener('click', function (e) {
    if (!$('searchWrap').contains(e.target)) sg.style.display = 'none';
  });

  function doSearch() {
    var q = input.value.trim().toLowerCase();
    matches = []; hlIdx = -1;
    if (q) {
      CITIES.forEach(function (c) {
        if (c.name.toLowerCase().indexOf(q) >= 0) matches.push({ type: 'city', it: c });
      });
      TOWNS.forEach(function (t) {
        if (t.town.toLowerCase().indexOf(q) >= 0 || (t.code && t.code.indexOf(q) >= 0)) matches.push({ type: 'town', it: t });
      });
      matches = matches.slice(0, 12);
    }
    if (matches.length) {
      sg.innerHTML = '';
      matches.forEach(function (m, i) {
        var d = document.createElement('div');
        d.className = 'sgItem';
        d.innerHTML = m.type === 'city'
          ? '<span><span class="tag">市县</span>' + m.it.name + '</span><span class="sub">' +
            m.it.towns + '个乡镇 · ' + fmtPop(m.it[popKey]) + ' 人</span>'
          : '<span><span class="tag">乡镇</span>' + m.it.town + '</span><span class="sub">' + m.it.city + '</span>';
        d.onclick = function () { pick(i); };
        sg.appendChild(d);
      });
      sg.style.display = 'block';
    } else {
      sg.style.display = 'none';
    }
  }
  function paintHl() {
    Array.prototype.forEach.call(sg.children, function (el, i) { el.classList.toggle('hl', i === hlIdx); });
  }
  function pick(i) {
    var m = matches[i]; if (!m) return;
    sg.style.display = 'none';
    input.value = m.type === 'city' ? m.it.name : m.it.town;
    if ((m.type === 'city' ? 'city' : 'town') !== scope) setScope(m.type === 'city' ? 'city' : 'town');
    zoomTo(m.it);
    selectItem(m.it, m.type, true);
  }

  /* ======================================================
   * 8. 启动
   * ====================================================== */
  $('tabAdmin').classList.toggle('active', mode === 'admin');
  $('tabArea').classList.toggle('active', mode === 'area');
  $('tabPop').classList.toggle('active', mode === 'pop');
  $('censusLabel').style.display = mode === 'pop' ? '' : 'none';
  $('censusTabs').style.display = mode === 'pop' ? '' : 'none';
  ['pop7', 'pop6', 'pop5'].forEach(function (key) {
    $('cen' + key.charAt(3)).classList.toggle('active', key === popKey);
  });
  $('scCity').classList.toggle('active', scope === 'city');
  $('scTown').classList.toggle('active', scope === 'town');
  window.addEventListener('resize', resize);
  resize();
  render();
})();

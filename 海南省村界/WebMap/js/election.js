/* ==========================================================
   省長選舉 · 得票地圖 · 應用邏輯（繁體版）
   依賴：data.js（window.HN_DATA，鄉鎮/市縣邊界）
        election_data.js（window.ELECTION_DATA，各鄉鎮得票數據）
   模塊：
     0. 繁化與數字   1. 數據與常量   2. 視圖與縮放   3. 統計聚合
     4. 著色         5. 渲染與圖例   6. 提示氣泡
     7. 選中與詳情   8. 範圍切換/匯總  9. 搜尋  10. 啟動  11. 一鍵更新
   ========================================================== */
(function () {
  'use strict';

  /* ======================================================
   * 0. 繁化（簡 → 繁）與數字排版
   *    data.js 為簡體，且被 map.html 共用 → 不改動源數據，
   *    只在本頁渲染時做字形轉換。
   * ====================================================== */
  // 成對存放：簡體在前、繁體在後（僅覆蓋本頁會出現的字）
  var S2T_PAIRS =
    '万萬东東临臨乌烏乐樂乡鄉云雲亚亞会會兰蘭兴興冯馮冲沖区區县縣发發号號吕呂吴吳' +
    '响響国國圣聖头頭宁寧宝寶带帶广廣庆慶开開报報旧舊枫楓栏欄样樣桥橋楼樓沟溝湾灣' +
    '滚滾满滿滨濱澜瀾灵靈烟煙爱愛独獨狮獅琼瓊畅暢礼禮禄祿积積红紅纪紀细細罗羅荣榮' +
    '莱萊莲蓮莺鶯营營蓝藍训訓调調谭譚边邊迈邁运運铺鋪锦錦镇鎮长長门門阁閣阳陽马馬' +
    '鳌鰲鸣鳴黄黃龙龍洒灑后後坝壩垦墾尧堯岗崗岭嶺袅裊台臺丰豐来來贸貿选選举舉华華铭銘';
  var S2T = {};
  (function () {
    for (var i = 0; i < S2T_PAIRS.length; i += 2) S2T[S2T_PAIRS.charAt(i)] = S2T_PAIRS.charAt(i + 1);
  })();
  function t2(s) {
    if (s == null) return '';
    return String(s).replace(/[\u4e00-\u9fff]/g, function (ch) { return S2T[ch] || ch; });
  }

  // 阿拉伯數字放大、單位漢字（萬/票）縮小
  function bigNum(s) { return '<span class="nbig">' + s + '</span>'; }
  function unit(s) { return '<span class="nunit">' + s + '</span>'; }
  function group3(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

  // 票數：≥ 1 萬 寫成 10萬600（萬 進位段不加千分位，與慣用寫法一致）；數字大、漢字小
  function fmtWan(n) {
    if (n == null) return '—';
    n = Math.round(n);
    if (n < 10000) return bigNum(group3(n));
    var w = Math.floor(n / 10000), r = n % 10000;
    return bigNum(w) + unit('萬') + (r ? bigNum(r) : '');
  }

  /* ======================================================
   * 1. 數據與常量
   * ====================================================== */
  var D = window.HN_DATA, E = window.ELECTION_DATA;
  if (!D || !E) {
    document.body.innerHTML = '<p style="padding:24px;font-family:sans-serif">缺少數據文件：需要 data.js 與 election_data.js。</p>';
    return;
  }
  var TOWNS = D.towns, CITIES = D.cities, ED = E.towns || {};
  var CAND = (E.candidates || []).slice().sort(function (a, b) { return a.group - b.group; });
  var NS = 'http://www.w3.org/2000/svg';

  // 4 組色階：第 1 組 = J、第 2 組 = L、第 3 組 = N、第 4 組 = P
  var RAMPS = [
    [[35, '#D9F6FF'], [40, '#A6E9FF'], [45, '#73D9FF'], [50, '#40C8FF'], [55, '#00C0F4'],
     [60, '#00A2E8'], [65, '#0080B8'], [70, '#006591'], [75, '#004B6B'], [80, '#003247'],
     [85, '#001F2E'], [100, '#010D29']],
    [[35, '#E8FFE0'], [40, '#CEFFC2'], [45, '#C0FFB1'], [50, '#A4FF90'], [55, '#78FF4F'],
     [60, '#68DE45'], [65, '#54B337'], [70, '#3C8027'], [75, '#2B5C1C'], [80, '#1D3D13'],
     [85, '#0F210A'], [100, '#071A09']],
    [[35, '#B3FFF0'], [40, '#00EBD1'], [45, '#00D9CA'], [50, '#00BFB2'], [55, '#00A89C'],
     [60, '#008080'], [65, '#006666'], [70, '#004C4C'], [75, '#003838'], [80, '#003030'],
     [85, '#002626'], [100, '#021F1F']],
    [[0, '#FFFFEB'], [35, '#FFFFEB'], [40, '#FFF8CC'], [45, '#FFF0A8'], [50, '#FFE780'],
     [55, '#FFDD55'], [60, '#FFF200'], [65, '#E6DA00'], [70, '#BFB500'], [75, '#999100'],
     [80, '#736D00'], [85, '#4D4900'], [100, '#383502']]
  ];
  // 各候選人「品牌色」（用於條形圖 / 名次徽標）
  var ACCENT = ['#1273c4', '#4ab52f', '#00a89c', '#dcbb00'];
  var GRAY = '#c9ced4';                 // 無數據區域
  var GRAY_STROKE = '#ffffff';

  function accentOf(group) { return ACCENT[(group - 1) % ACCENT.length]; }
  function rampOf(group) { return RAMPS[(group - 1) % RAMPS.length]; }

  // 得票率 → 色階顏色（階梯取色：落在 [t_i, t_{i+1}) 用第 i 檔）
  function rampColor(v, ramp) {
    var c = ramp[0][1];
    for (var i = 0; i < ramp.length; i++) {
      if (v >= ramp[i][0]) c = ramp[i][1]; else break;
    }
    return c;
  }

  function fmtNum(n) {
    if (n == null) return '—';
    return bigNum(group3(Math.round(n)));
  }
  function fmtRate(v) { return v == null ? '—' : v.toFixed(2) + '%'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }
  // 地名：簡 → 繁 + 轉義
  function nm(s) { return esc(t2(s)); }
  function $(id) { return document.getElementById(id); }

  // 市縣名簡稱：去掉民族自治字樣，只保留「XX + 縣／市」（先簡體替換，再轉繁）
  var AUTON = /(黎族苗族|黎族|苗族|回族|壮族|瑶族|彝族|白族|傣族|土家族|朝鲜族|蒙古族|满族|侗族|布依族|哈尼族|傈僳族|拉祜族|佤族|纳西族|畲族|京族|仡佬族|毛南族|东乡族|撒拉族|保安族|裕固族|锡伯族|塔吉克族|柯尔克孜族|塔塔尔族|乌孜别克族|俄罗斯族|鄂温克族|鄂伦春族|赫哲族|门巴族|珞巴族|基诺族|德昂族|阿昌族|普米族|怒族|独龙族|羌族|土族|达斡尔族)自治([县市州])$/;
  function shortCity(name) {
    return t2(String(name).replace(AUTON, '$2'));
  }

  /* ======================================================
   * 2. 視圖與縮放
   * ====================================================== */
  var bb = { x0: 0, y0: 0, x1: D.viewW, y1: D.viewH };
  var W = 0, H = 0, BASE_K = 1;
  var mapArea = $('mapArea'), svg = $('svg');
  var view = { k: 1, tx: 0, ty: 0, userMoved: false };
  var gRoot = null, soEl = null;
  var scope = 'city';          // city | town
  var selected = null;         // {type:'city'|'town', item}

  try {
    var qs = new URLSearchParams(location.search);
    if (qs.get('scope') === 'town') scope = 'town';
  } catch (e) {}

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
      t.setAttribute('font-size', parseFloat(t.dataset.fs) * inv);
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

  // ---------- 平移 / 滾輪 / 控件縮放 ----------
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
    zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.25 : 0.8);
  }, { passive: false });
  $('zin').onclick = function () { zoomCenter(1.3); };
  $('zout').onclick = function () { zoomCenter(1 / 1.3); };
  $('zreset').onclick = function () { resetView(); apply(); };
  function zoomAt(px, py, f) {
    var nk = Math.max(1, Math.min(60, view.k * f));
    view.tx = px - (px - view.tx) * (nk / view.k);
    view.ty = py - (py - view.ty) * (nk / view.k);
    view.k = nk; view.userMoved = true; apply();
  }
  function zoomCenter(f) {
    var nk = Math.max(1, Math.min(60, view.k * f));
    view.tx = W / 2 - (W / 2 - view.tx) * (nk / view.k);
    view.ty = H / 2 - (H / 2 - view.ty) * (nk / view.k);
    view.k = nk; view.userMoved = true; apply();
  }

  // ---------- 點擊海面取消選中 ----------
  var CLEAR_MARGIN = 80;
  svg.addEventListener('click', function (e) {
    if (e.target && e.target.closest && e.target.closest('path.region')) return;
    if (justDragged) { justDragged = false; return; }
    var r = svg.getBoundingClientRect();
    var px = e.clientX - r.left, py = e.clientY - r.top;
    var k = BASE_K * view.k;
    var x0 = bb.x0 * k + view.tx - CLEAR_MARGIN, y0 = bb.y0 * k + view.ty - CLEAR_MARGIN;
    var x1 = bb.x1 * k + view.tx + CLEAR_MARGIN, y1 = bb.y1 * k + view.ty + CLEAR_MARGIN;
    if (px < x0 || px > x1 || py < y0 || py > y1) return;
    clearSelection();
  });

  /* ======================================================
   * 3. 統計聚合
   * ====================================================== */
  var NC = CAND.length;

  function aggTowns(list) {
    var n = 0, sumG = 0, sumF = 0, sumD = 0, votes = new Array(NC).fill(0);
    list.forEach(function (t) {
      var e = ED[t.code]; if (!e) return;
      n++; sumG += e.G; sumF += (e.F || 0); sumD += (e.D || 0);
      for (var i = 0; i < NC; i++) votes[i] += (e.votes[i] || 0);
    });
    if (!n) return null;
    return {
      n: n, G: sumG, F: sumF, D: sumD, votes: votes,
      rates: votes.map(function (v) { return sumG ? v / sumG * 100 : 0; }),
      turnout: sumD ? sumF / sumD * 100 : null,
      validRate: sumF ? sumG / sumF * 100 : null
    };
  }
  function statOfTown(code) {
    var e = ED[code]; if (!e) return null;
    return {
      n: 1, G: e.G, F: e.F || 0, D: e.D || 0, votes: e.votes.slice(), rates: e.rates.slice(),
      turnout: (e.D && e.F != null) ? e.F / e.D * 100 : null,
      validRate: (e.F && e.G != null) ? e.G / e.F * 100 : null
    };
  }
  function leadIdx(rates) {
    var bi = -1, bv = -Infinity;
    for (var i = 0; i < rates.length; i++) if (rates[i] > bv) { bv = rates[i]; bi = i; }
    return bi;
  }
  function aggColor(agg) {
    if (!agg) return GRAY;
    var li = leadIdx(agg.rates);
    if (li < 0) return GRAY;
    return rampColor(agg.rates[li], rampOf(CAND[li].group));
  }

  var cityStats = {}, townStats = {}, provinceStats = null;
  function precomputeAll() {
    cityStats = {}; townStats = {};
    TOWNS.forEach(function (t) { townStats[t.code] = statOfTown(t.code); });
    CITIES.forEach(function (c) {
      cityStats[c.name] = aggTowns(TOWNS.filter(function (t) { return t.city === c.name; }));
    });
    provinceStats = aggTowns(TOWNS);
  }
  function statOfCity(name) { return cityStats[name] || null; }
  precomputeAll();

  /* ======================================================
   * 4. 渲染與圖例
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

  function render() {
    svg.innerHTML = '';
    hideTip();
    soEl = null;
    gRoot = document.createElementNS(NS, 'g');
    gRoot.id = 'vp';
    svg.appendChild(gRoot);

    var items = scope === 'city' ? CITIES : TOWNS;

    items.forEach(function (it) {
      var agg = scope === 'city' ? statOfCity(it.name) : statOfTown(it.code);
      var p = document.createElementNS(NS, 'path');
      p.setAttribute('d', pathD(it.polys));
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('class', 'region');
      p.setAttribute('stroke', agg ? '#fff' : GRAY_STROKE);
      p.dataset.w = scope === 'city' ? 1.5 : 1;
      p.setAttribute('fill', aggColor(agg));
      p.addEventListener('mouseenter', function (e) { showTip(it, agg, e); });
      p.addEventListener('mousemove', moveTip);
      p.addEventListener('mouseleave', hideTip);
      p.addEventListener('click', function () {
        if (!justDragged) selectItem(it, scope === 'city' ? 'city' : 'town', true);
      });
      gRoot.appendChild(p);
    });

    // 名稱標註
    items.forEach(function (it) {
      var t = document.createElementNS(NS, 'text');
      t.setAttribute('class', 'rlabel ' + (scope === 'city' ? 'city' : 'town'));
      t.setAttribute('x', it.lp[0].toFixed(2));
      t.setAttribute('y', it.lp[1].toFixed(2));
      t.dataset.fs = scope === 'city' ? 13 : 10;
      t.dataset.kind = scope === 'city' ? 'city' : 'town';
      t.textContent = scope === 'city' ? shortCity(it.name) : t2(it.town);
      gRoot.appendChild(t);
    });

    renderLegend();
    apply();
  }

  function renderLegend() {
    var lg = $('legend');
    lg.innerHTML = '';
    var t = document.createElement('div');
    t.className = 't';
    t.textContent = '領先候選人 · 得票率（%）';
    lg.appendChild(t);

    CAND.forEach(function (c) {
      var ramp = rampOf(c.group);
      var row = document.createElement('div');
      row.className = 'lgRow';
      var nmEl = document.createElement('span');
      nmEl.className = 'lgName';
      nmEl.textContent = t2(c.name);
      row.appendChild(nmEl);
      var cells = document.createElement('div');
      cells.className = 'lgCells';
      ramp.forEach(function (pair) {
        var d = document.createElement('div');
        d.className = 'cell';
        d.style.background = pair[1];
        d.title = '≥ ' + pair[0] + '% → ' + pair[1];
        cells.appendChild(d);
      });
      row.appendChild(cells);
      lg.appendChild(row);

      var sc = document.createElement('div');
      sc.className = 'lgScale';
      sc.innerHTML = '<span>' + ramp[0][0] + '%</span><span>' +
        ramp[ramp.length - 1][0] + '%</span>';
      lg.appendChild(sc);
    });

    var g = document.createElement('div');
    g.className = 'lgGray';
    g.innerHTML = '<span class="sw" style="background:' + GRAY + '"></span>無數據（該鄉鎮未進行選舉）';
    lg.appendChild(g);
  }

  /* ======================================================
   * 5. 提示氣泡
   * ====================================================== */
  var tip = $('tip');
  function showTip(it, agg, e) {
    var name = scope === 'city' ? shortCity(it.name) : t2(it.town);
    var html;
    if (!agg) {
      html = esc(name) + '<br><span style="color:#bbb">無數據</span>';
    } else {
      var li = leadIdx(agg.rates);
      var c = CAND[li];
      html = esc(name) + '<br><span style="color:' + accentOf(c.group) + '">●</span> ' +
        esc(t2(c.name)) + ' <b>' + fmtRate(agg.rates[li]) + '</b>' +
        ' · ' + fmtWan(agg.votes[li]) + unit('票');
    }
    tip.innerHTML = html;
    tip.style.display = 'block';
    moveTip(e);
  }
  function moveTip(e) {
    var r = mapArea.getBoundingClientRect();
    tip.style.left = (e.clientX - r.left + 14) + 'px';
    tip.style.top = (e.clientY - r.top + 14) + 'px';
  }
  function hideTip() { tip.style.display = 'none'; }

  /* ======================================================
   * 6. 選中與詳情（右側卡片）
   * ====================================================== */
  function selectItem(it, type, open) {
    selected = { type: type, item: it };
    if (soEl) soEl.remove();
    soEl = document.createElementNS(NS, 'path');
    soEl.id = 'selOutline';
    soEl.setAttribute('d', pathD(it.polys));
    gRoot.appendChild(soEl);
    apply();

    if (type === 'city') renderDetail(statOfCity(it.name), shortCity(it.name), '海南省 · 市縣', it.name);
    else renderDetail(statOfTown(it.code), t2(it.town), '海南省 · ' + shortCity(it.city || ''), it.town);
    markSummary();
  }

  function clearSelection() {
    selected = null;
    if (soEl) { soEl.remove(); soEl = null; }
    renderDetail(provinceStats, '海南省（全省）', '海南省 · 全省匯總', null);
    markSummary();
  }

  function renderDetail(agg, title, crumb, regionName) {
    var box = $('detail');
    box.innerHTML = '';

    var head = document.createElement('div');
    head.className = 'cardHead';
    head.innerHTML = '<h2>' + esc(title) + '</h2><span class="region">' + esc(E.year) + ' 年</span>';
    box.appendChild(head);

    var cr = document.createElement('div');
    cr.className = 'crumb';
    cr.textContent = crumb;
    box.appendChild(cr);

    if (!agg) {
      var em = document.createElement('div');
      em.className = 'empty';
      em.textContent = '該區域暫無選舉數據（地圖中以灰色顯示）。';
      box.appendChild(em);
      return;
    }

    // 候選人按得票數降序
    var order = CAND.map(function (_, i) { return i; })
      .sort(function (a, b) { return (agg.votes[b] || 0) - (agg.votes[a] || 0); });
    var li = leadIdx(agg.rates);

    order.forEach(function (i, rank) {
      var c = CAND[i], ac = accentOf(c.group);
      var row = document.createElement('div');
      row.className = 'cand';
      var lead = (i === li) ? '<span class="leadTag" style="background:' + ac + '">領先</span>' : '';
      row.innerHTML =
        '<span class="badge" style="background:' + ac + '">' + (rank + 1) + '</span>' +
        '<div class="candBody">' +
          '<div class="candTop">' +
            '<span class="nm">' + esc(t2(c.name)) + lead + '</span>' +
            '<span class="votes">' + fmtWan(agg.votes[i]) + unit('票') + '</span>' +
          '</div>' +
          '<div class="bar"><i style="width:' + Math.min(100, agg.rates[i]).toFixed(2) + '%;background:' + ac + '"></i></div>' +
          '<div class="rate"><span>得票率 <b>' + fmtRate(agg.rates[i]) + '</b></span>' +
            '<span>得票數 ' + fmtWan(agg.votes[i]) + '</span></div>' +
        '</div>';
      box.appendChild(row);
    });

    var foot = document.createElement('div');
    foot.className = 'foot';
    var parts = [];
    parts.push('<span>投票率 <b>' + fmtRate(agg.turnout) + '</b></span>');
    parts.push('<span>投票數 <b>' + fmtWan(agg.F) + '</b></span>');
    parts.push('<span>有效票 <b>' + fmtWan(agg.G) + '</b></span>');
    if (agg.validRate != null) parts.push('<span>有效票率 <b>' + fmtRate(agg.validRate) + '</b></span>');
    parts.push('<span>選舉人數 <b>' + fmtWan(agg.D) + '</b></span>');
    if (regionName && scope === 'city') parts.push('<span>含 <b>' + agg.n + '</b> 個鄉鎮</span>');
    foot.innerHTML = parts.join('');
    box.appendChild(foot);
  }

  /* ======================================================
   * 7. 匯總表（全省 + 各市縣）
   * ====================================================== */
  function buildSummary() {
    var wrap = $('summary');
    wrap.innerHTML = '';
    var h = document.createElement('div');
    h.className = 'sumTitle';
    h.textContent = '全省 · 各市縣匯總';
    wrap.appendChild(h);

    var tbl = document.createElement('table');
    tbl.className = 'tbl';
    tbl.innerHTML = '<thead><tr><th>區域</th><th>領先</th><th class="num">得票率</th><th class="num">投票率</th></tr></thead>';
    var tb = document.createElement('tbody');

    function addRow(key, name, agg) {
      var tr = document.createElement('tr');
      tr.dataset.key = key;
      if (!agg) {
        tr.innerHTML = '<td>' + esc(name) + '</td><td colspan="3" class="dim">無數據</td>';
      } else {
        var li = leadIdx(agg.rates), c = CAND[li];
        tr.innerHTML =
          '<td>' + esc(name) + '</td>' +
          '<td class="lead"><span class="dot" style="background:' + accentOf(c.group) + '"></span>' + esc(t2(c.name)) + '</td>' +
          '<td class="num">' + fmtRate(agg.rates[li]) + '</td>' +
          '<td class="num">' + fmtRate(agg.turnout) + '</td>';
      }
      tr.onclick = function () {
        var it = null;
        if (key === '__all__') { clearSelection(); resetView(); apply(); return; }
        it = CITIES.filter(function (c) { return c.name === key; })[0];
        if (!it) return;
        if (scope !== 'city') setScope('city', true);
        zoomTo(it); selectItem(it, 'city', true);
      };
      tb.appendChild(tr);
    }

    addRow('__all__', '全省', provinceStats);
    CITIES.forEach(function (c) { addRow(c.name, shortCity(c.name), cityStats[c.name]); });
    tbl.appendChild(tb);
    wrap.appendChild(tbl);

    var note = document.createElement('div');
    note.className = 'sumNote';
    var nd = CITIES.filter(function (c) { return !!cityStats[c.name]; }).length;
    note.textContent = '共 ' + CITIES.length + ' 個市縣，其中 ' + nd +
      ' 個有選舉數據；點擊行可定位。數據源：' + t2(E.source || '省长选举.xlsx') + '。';
    wrap.appendChild(note);
  }

  function markSummary() {
    var rows = $('summary').querySelectorAll('tr[data-key]');
    Array.prototype.forEach.call(rows, function (tr) {
      tr.classList.toggle('on', !!selected && selected.type === 'city' && tr.dataset.key === selected.item.name);
    });
  }

  /* ======================================================
   * 8. 範圍切換
   * ====================================================== */
  function setScope(s, keep) {
    scope = s;
    if (!keep) {
      selected = null; if (soEl) { soEl.remove(); soEl = null; }
      renderDetail(provinceStats, '海南省（全省）', '海南省 · 全省匯總', null);
      markSummary();
    }
    $('scCity').classList.toggle('active', s === 'city');
    $('scTown').classList.toggle('active', s === 'town');
    resetView();
    render();
  }
  $('scCity').onclick = function () { setScope('city'); };
  $('scTown').onclick = function () { setScope('town'); };

  $('chkLabels').onchange = function () {
    mapArea.classList.toggle('nolabels', !this.checked);
  };

  $('btnSummary').onclick = function () {
    $('summary').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  /* ======================================================
   * 9. 搜尋
   * ====================================================== */
  var input = $('search'), sg = $('suggest');
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
    // 查詢詞也做繁化，簡繁輸入都能命中
    var q = t2(input.value.trim().toLowerCase());
    matches = []; hlIdx = -1;
    if (q) {
      CITIES.forEach(function (c) {
        // 簡稱（去自治字樣）也一併比對，輸入「白沙縣」「白沙」都能命中
        if (t2(c.name).toLowerCase().indexOf(q) >= 0 ||
            shortCity(c.name).toLowerCase().indexOf(q) >= 0) {
          matches.push({ type: 'city', it: c });
        }
      });
      TOWNS.forEach(function (t) {
        if (t2(t.town).toLowerCase().indexOf(q) >= 0 || (t.code && t.code.indexOf(q) >= 0)) matches.push({ type: 'town', it: t });
      });
      matches = matches.slice(0, 12);
    }
    if (matches.length) {
      sg.innerHTML = '';
      matches.forEach(function (m, i) {
        var d = document.createElement('div');
        d.className = 'sgItem';
        var agg = m.type === 'city' ? statOfCity(m.it.name) : statOfTown(m.it.code);
        var sub;
        if (!agg) sub = '無數據';
        else { var li = leadIdx(agg.rates); sub = t2(CAND[li].name) + ' ' + fmtRate(agg.rates[li]); }
        d.innerHTML = m.type === 'city'
          ? '<span><span class="tag">市縣</span>' + esc(shortCity(m.it.name)) + '</span><span class="sub">' + esc(sub) + '</span>'
          : '<span><span class="tag">鄉鎮</span>' + nm(m.it.town) + '</span><span class="sub">' + esc(shortCity(m.it.city)) + '</span>';
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
    input.value = m.type === 'city' ? shortCity(m.it.name) : t2(m.it.town);
    var want = m.type === 'city' ? 'city' : 'town';
    if (want !== scope) setScope(want, true);
    zoomTo(m.it);
    selectItem(m.it, m.type, true);
  }

  /* ======================================================
   * 11. 一鍵更新：呼叫本地 server.py 重算 xlsx，並就地刷新頁面數據
   *      （不整頁重載：只重新拉 election_data.js 後重建統計與渲染）
   * ====================================================== */
  function setBrand() {
    $('yearTag').textContent = E.year + ' 年 · ' + t2(E.source || '');
  }

  function reloadElectionData(done) {
    var s = document.createElement('script');
    s.src = './election_data.js?_=' + Date.now();
    s.onload = function () {
      E = window.ELECTION_DATA;
      ED = E.towns || {};
      CAND = (E.candidates || []).slice().sort(function (a, b) { return a.group - b.group; });
      NC = CAND.length;
      precomputeAll();
      setBrand();
      buildSummary();
      selected = null; if (soEl) { soEl.remove(); soEl = null; }
      render();
      clearSelection();
      if (done) done();
    };
    s.onerror = function () { location.reload(); };   // 兜底：整页重载
    document.head.appendChild(s);
  }

  var toastEl = $('toast'), toastTimer = null;
  function toast(msg, bad) {
    toastEl.textContent = msg;
    toastEl.className = 'show' + (bad ? ' bad' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.className = ''; }, bad ? 7000 : 4000);
  }

  var btnRefresh = $('btnRefresh');
  btnRefresh.onclick = function () {
    if (location.protocol === 'file:') {
      toast('本頁是以 file:// 開啟的，無法呼叫更新接口。\n請先執行 python server.py，' +
            '再從 http://127.0.0.1:8765/Election.html 開啟。', true);
      return;
    }
    var label = btnRefresh.textContent;
    btnRefresh.disabled = true;
    btnRefresh.textContent = '更新中…';
    fetch('./api/refresh', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) throw new Error(j.error || '更新失敗');
        return new Promise(function (res) { reloadElectionData(res); })
          .then(function () { toast('已更新 · ' + j.msg); });
      })
      .catch(function (e) {
        toast('更新失敗：' + (e && e.message ? e.message : e), true);
      })
      .then(function () {
        btnRefresh.disabled = false;
        btnRefresh.textContent = label;
      });
  };

  /* ======================================================
   * 12. 啟動
   * ====================================================== */
  $('scCity').classList.toggle('active', scope === 'city');
  $('scTown').classList.toggle('active', scope === 'town');
  setBrand();
  buildSummary();
  window.addEventListener('resize', resize);
  resize();
  render();
  clearSelection();
})();

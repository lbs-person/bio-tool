/* 离线生物分类查询 · Android 版界面逻辑
 *
 * 数据完全来自本地：assets/bio.db（SQLite，sql.js 在 WebView 里查）与
 * assets/images/（压缩后的图）。不发任何网络请求，飞行模式下也能用。
 */
'use strict';

const DB_URL = 'assets/bio.db';

/* bio.db 里的 image_path 存的是相对 output/ 的规范路径（形如
   images/00/09/xxx.webp），资料本身与 output/ 保持一致，便于和 Python
   侧（query.py / gui.py）共用同一份数据。资产在 App 里落在
   www/assets/images/，所以取图时要补上这个前缀。 */
const IMG_BASE = 'assets/';

const PAGE = 60;

let db = null;
let results = [];
let shown = 0;

const $ = (id) => document.getElementById(id);

/* ---------------- 初始化 ---------------- */

async function boot() {
  try {
    setMsg('正在载入 SQLite 引擎…');
    const SQL = await initSqlJs({
      locateFile: (f) => f,          // sql-wasm.wasm 与页面同目录
    });

    setMsg('正在载入数据库…');
    const buf = await fetch(DB_URL).then((r) => {
      if (!r.ok) throw new Error(`取数据库失败 HTTP ${r.status}`);
      return r.arrayBuffer();
    });

    setMsg('正在打开数据库…');
    db = new SQL.Database(new Uint8Array(buf));

    const n = scalar('SELECT COUNT(*) FROM entry');
    const w = scalar('SELECT COUNT(*) FROM entry WHERE has_image=1');
    $('count').textContent = `${fmt(n)} 条 · 有图 ${fmt(w)}`;

    $('loading').hidden = true;
    $('q').focus();
  } catch (e) {
    setMsg('载入失败：' + e.message);
    console.error(e);
  }
}

function setMsg(t) { $('loadmsg').textContent = t; }

function scalar(sql, params) {
  const r = db.exec(sql, params);
  return r.length ? r[0].values[0][0] : null;
}

function fmt(n) { return Number(n).toLocaleString('zh-CN'); }

/* 把数据库里的规范路径变成 App 内可加载的 URL */
function imgUrl(p) { return IMG_BASE + String(p).replace(/^\/+/, ''); }

/* ---------------- 搜索 ---------------- */

/* LIKE 的通配符要转义，否则用户输入 % 会匹配全部。
   转义符用 ! 而不是反斜杠：写在 JS 模板字符串里时，'\\' 会被解释成单个
   反斜杠，SQLite 再把它当成转义引号，于是直接报
   "ESCAPE expression must be a single character"，搜索整个失效。 */
function likeArg(s) {
  return '%' + s.replace(/[!%_]/g, (m) => '!' + m) + '%';
}

const SEARCH_SQL = `
SELECT e.id, e.sci_name, e.cn_name, e.is_subsp, e.has_image,
       t.family_cn, t.genus_cn, i.image_path
FROM entry e
LEFT JOIN tax t ON t.tax_id = e.tax_id
LEFT JOIN img i ON i.img_id = e.img_id
WHERE (?1 = '' OR e.sci_name LIKE ?2 ESCAPE '!' OR e.cn_name LIKE ?2 ESCAPE '!')
  AND (?3 = 0 OR e.has_image = 1)
ORDER BY (e.cn_name IS NULL OR e.cn_name = ''), e.cn_name, e.sci_name
LIMIT ?4 OFFSET ?5`;

function runSearch(append) {
  if (!db) return;
  const q = $('q').value.trim();
  const only = $('onlyImg').checked ? 1 : 0;

  if (!append) { shown = 0; results = []; }

  const r = db.exec(SEARCH_SQL, [q, likeArg(q), only, PAGE, shown]);
  const rows = r.length ? r[0].values : [];

  if (!append && rows.length === 0) {
    $('list').innerHTML = `<div class="empty">没有匹配「${esc(q)}」的结果</div>`;
    $('btnMore').hidden = true;
    $('count').textContent = '0 条结果';
    return;
  }

  if (!append) $('list').innerHTML = '';
  for (const row of rows) results.push(row);
  shown += rows.length;
  renderRows(rows, append ? $('list').children.length : 0);

  $('btnMore').hidden = rows.length < PAGE;
  $('count').textContent = `${fmt(shown)}+ 条结果`;
}

function renderRows(rows, startIdx) {
  const frag = document.createDocumentFragment();
  rows.forEach((row, k) => {
    const [id, sci, cn, isSub, hasImg, family, genus, imgPath] = row;
    const el = document.createElement('div');
    el.className = 'row';
    el.dataset.idx = startIdx + k;

    const thumb = hasImg && imgPath
      ? `<img src="${esc(imgUrl(imgPath))}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'na',textContent:'图缺'}))">`
      : `<span class="na">无图</span>`;

    el.innerHTML = `
      <div class="thumb">${thumb}</div>
      <div class="meta">
        <div class="sci">${esc(sci)}${isSub ? ' <span class="sub">亚种</span>' : ''}</div>
        ${cn ? `<div class="cn">${esc(cn)}</div>` : ''}
        <div class="sub">${esc([family, genus].filter(Boolean).join(' · '))}</div>
      </div>`;
    el.addEventListener('click', () => showDetail(id));
    frag.appendChild(el);
  });
  $('list').appendChild(frag);
}

/* ---------------- 详情 ---------------- */

const DETAIL_SQL = `
SELECT e.sci_name, e.cn_name, e.source, e.is_subsp,
       t.kingdom_cn, t.phylum_cn, t.class_cn, t.order_cn, t.family_cn, t.genus_cn,
       t.kingdom_latin, t.phylum_latin, t.class_latin, t.order_latin,
       t.family_latin, t.genus_latin,
       i.image_path, i.author, i.license, i.license_url, i.page_url
FROM entry e
LEFT JOIN tax t ON t.tax_id = e.tax_id
LEFT JOIN img i ON i.img_id = e.img_id
WHERE e.id = ?`;

function showDetail(id) {
  const r = db.exec(DETAIL_SQL, [id]);
  if (!r.length) return;
  const v = r[0].values[0];
  const [sci, cn, source, isSub,
    kCn, pCn, cCn, oCn, fCn, gCn,
    kLat, pLat, cLat, oLat, fLat, gLat,
    imgPath, author, license, licUrl, pageUrl] = v;

  $('dSci').textContent = sci;
  $('dCn').textContent = [cn, isSub ? '亚种' : ''].filter(Boolean).join('　');

  const levels = [['界', kCn, kLat], ['门', pCn, pLat], ['纲', cCn, cLat],
                  ['目', oCn, oLat], ['科', fCn, fLat], ['属', gCn, gLat]];
  $('dTax').innerHTML = levels
    .filter(([, cnv, latv]) => cnv || latv)
    .map(([label, cnv, latv]) =>
      `<dt>${label}</dt><dd>${esc(cnv || '')}${cnv && latv ? ' ' : ''}<span class="lat">${esc(latv || '')}</span></dd>`)
    .join('');

  if (imgPath) {
    $('dImgWrap').innerHTML =
      `<img src="${esc(imgUrl(imgPath))}" alt="${esc(sci)}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'miss',textContent:'图片文件缺失'}))">`;
  } else {
    $('dImgWrap').innerHTML = `<div class="miss">该物种暂无合规授权图片</div>`;
  }

  const parts = [];
  if (author) parts.push(`<div><b>作者</b> ${esc(author)}</div>`);
  if (license) {
    parts.push(`<div><b>许可</b> ${licUrl ? `<a href="${esc(licUrl)}" target="_blank" rel="noopener">${esc(license)}</a>` : esc(license)}</div>`);
  }
  if (pageUrl) parts.push(`<div><b>来源页</b> <a href="${esc(pageUrl)}" target="_blank" rel="noopener">${esc(pageUrl)}</a></div>`);
  if (source) parts.push(`<div><b>名录来源</b> ${esc(source)}</div>`);
  $('dCredit').innerHTML = parts.join('') || '<div>无版权信息</div>';

  $('detail').hidden = false;
  $('detail').scrollTop = 0;
}

/* ---------------- 工具 ---------------- */

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ---------------- 事件绑定 ---------------- */

$('btnSearch').addEventListener('click', () => runSearch(false));
$('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(false); });
$('q').addEventListener('search', () => runSearch(false));
$('onlyImg').addEventListener('change', () => runSearch(false));
$('btnMore').addEventListener('click', () => runSearch(true));
$('btnClose').addEventListener('click', () => { $('detail').hidden = true; });
$('detail').addEventListener('click', (e) => {
  if (e.target === $('detail')) $('detail').hidden = true;
});
/* 安卓返回键：优先关详情面板 */
window.addEventListener('popstate', () => {
  if (!$('detail').hidden) { $('detail').hidden = true; history.pushState(null, ''); }
});
history.pushState(null, '');

boot();

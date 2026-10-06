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

/* 查询俗名字典。名录用的是志书正式名（《中国动物志》等），与日常叫法常对不上：
     鲤 ⊂ 鲤鱼  但  鲤鱼 ⊄ 鲤   ->   直接搜「鲤鱼」搜不到记录「鲤」
   字典里存的是实测搜不到的俗名到正式名的映射。 */
const ALIAS_SQL = `
SELECT target FROM alias
WHERE ?1 LIKE '%' || term || '%'
LIMIT 3`;

/* 搜索与排序。
   pattern 是用逗号连接的一组 LIKE 模式（原词 + 俗名映射），命中任一即可。
   排序分四档，解决「搜『鱼』出来前 60 条全是 XX鱼蚤」的问题：
     0 = 名称完全等于输入        （搜「鲤」直接给「鲤」）
     1 = 名称以输入开头          （搜「鲤」先给「鲤形目」相关，再才是别的）
     2 = 有图                    （同名情况下优先能看图的）
     3 = 名称短                   越短越可能是正主，长名多是「XX鱼寄生虫」
   实测：搜「鱼」有 1,427 条，原排序下前面全是寄生生物，现在正主排最前。 */
const SEARCH_SQL = `
SELECT e.id, e.sci_name, e.cn_name, e.is_subsp, e.has_image,
       t.family_cn, t.genus_cn, i.image_path,
       CASE
         WHEN e.cn_name = ?1 OR e.sci_name = ?1 THEN 0
         WHEN e.cn_name LIKE ?6 ESCAPE '!' OR e.sci_name LIKE ?6 ESCAPE '!' THEN 1
         WHEN e.has_image = 1 THEN 2
         ELSE 3
       END AS rank
FROM entry e
LEFT JOIN tax t ON t.tax_id = e.tax_id
LEFT JOIN img i ON i.img_id = e.img_id
WHERE (?1 = ''
       OR e.sci_name LIKE ?2 ESCAPE '!' OR e.cn_name LIKE ?2 ESCAPE '!'
       OR e.sci_name LIKE ?3 ESCAPE '!' OR e.cn_name LIKE ?3 ESCAPE '!'
       OR e.sci_name LIKE ?4 ESCAPE '!' OR e.cn_name LIKE ?4 ESCAPE '!'
       OR e.sci_name LIKE ?5 ESCAPE '!' OR e.cn_name LIKE ?5 ESCAPE '!')
  AND (?7 = 0 OR e.has_image = 1)
ORDER BY rank,
         (e.cn_name IS NULL OR e.cn_name = ''),
         LENGTH(e.cn_name), e.cn_name, e.sci_name
LIMIT ?8 OFFSET ?9`;

function runSearch(append) {
  if (!db) return;
  const q = $('q').value.trim();
  const only = $('onlyImg').checked ? 1 : 0;

  if (!append) { shown = 0; results = []; }

  /* 查俗名映射：用户输入里若含某个俗名，把对应正式名也拿来一起匹配。
     例如输入「鲤鱼」-> 额外用「鲤」匹配，从而找到记录「鲤」。 */
  let targets = [];
  if (q) {
    try {
      const ar = db.exec(ALIAS_SQL, [q]);
      if (ar.length) targets = ar[0].values.map((v) => v[0]).filter(Boolean);
    } catch (e) { /* 老版本数据库没有 alias 表，忽略 */ }
  }

  /* 没被俗名用到的槽位需要一个「匹配不到任何东西」的模式。
     这里踩过一个坑：一开始用 '\u0000' 当占位符，结果 SQLite 把字符串里的
     NUL 当成结尾，模式 '%\0%' 被截断成 '%'，等于匹配了一切——搜「鲤」时
     精确命中之后会跟一堆毫不相干但恰好有图的物种（构、桉、樟…）。
     改用控制字符 \u0001：它不会出现在任何物种名里，且不是字符串终止符。 */
  const NEVER = '\u0001';
  const pat = (i) =>
    '%' + (targets[i] || NEVER).replace(/[!%_]/g, (m) => '!' + m) + '%';
  const prefix = q ? q.replace(/[!%_]/g, (m) => '!' + m) + '%' : '%';

  const r = db.exec(SEARCH_SQL,
    [q, likeArg(q), pat(0), pat(1), pat(2), prefix, only, PAGE + 1, shown]);
  let rows = r.length ? r[0].values : [];

  /* 多取一条用来判断「还有没有下一批」。若改用「返回数 < PAGE」判断，
     总数正好是 PAGE 的整数倍时，「加载更多」会错误地消失。 */
  const hasMore = rows.length > PAGE;
  if (hasMore) rows = rows.slice(0, PAGE);

  if (!append && rows.length === 0) {
    let hint = '';
    if (q) {
      // 猜一下为什么没结果，比干巴巴一句「没找到」有用
      if (/[鱼鸟虫树花草菌蛇蛙蟹虾贝螺蜂蚁蝶蛾]/.test(q)) {
        hint = '<div style="margin-top:8px;font-size:13px">'
             + '试试去掉最后一个字，或换用志书里的正式名（如「鲤鱼」→「鲤」）。'
             + '</div>';
      } else {
        hint = '<div style="margin-top:8px;font-size:13px">'
             + '本名录收录的是中国野生动物与部分植物、真菌，'
             + '不含家畜与栽培品种（如家猫、牛、马）。</div>';
      }
    }
    $('list').innerHTML =
      `<div class="empty">没有匹配「${esc(q)}」的结果${hint}</div>`;
    $('btnMore').hidden = true;
    $('count').textContent = '0 条结果';
    return;
  }

  if (!append) $('list').innerHTML = '';
  for (const row of rows) results.push(row);
  shown += rows.length;
  renderRows(rows, append ? $('list').children.length : 0);

  $('btnMore').hidden = !hasMore;
  const via = targets.length ? `（含俗名映射：${targets.join('、')}）` : '';
  $('count').textContent = hasMore
    ? `已显示 ${fmt(shown)} 条${via}`
    : `共 ${fmt(shown)} 条${via}`;
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

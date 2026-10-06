/**
 * copy-assets.js
 *
 * 把 APK 需要的两类外部资产放进 www/：
 *
 *   1. sql.js 的运行时（sql-wasm.js + sql-wasm.wasm）—— 从 npm 下载。
 *      必须内置，不能引 CDN，否则断网就用不了，违背离线工具的定位。
 *   2. dist_assets/（bio.db + 压缩后的 images/）—— 由 scripts/build_assets.py 生成。
 *
 * Capacitor 打包时会整体复制 www/ 到 Android 工程的 assets/public/，
 * 所以这两类东西放进来就能被 App 直接读到。
 *
 * 用法：
 *     node scripts/copy-assets.js
 *     node scripts/copy-assets.js --skip-db     # 只补 sql.js，不复制大文件
 */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');
const { execFileSync } = require('child_process');

const HERE = __dirname;
const ROOT = path.resolve(HERE, '..', '..');          // 仓库根目录
const WWW = path.resolve(HERE, '..', 'www');
const DIST = path.join(ROOT, 'dist_assets');

const SQLJS_VERSION = '1.13.0';
const SQLJS_BASE = `https://cdn.jsdelivr.net/npm/sql.js@${SQLJS_VERSION}/dist`;

const skipDb = process.argv.includes('--skip-db');

function human(n) {
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${u[i]}`;
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const tmp = dest + '.part';
    const file = fs.createWriteStream(tmp);
    https.get(url, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close();
        fs.unlinkSync(tmp);
        return download(res.headers.location, dest).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        file.close();
        return reject(new Error(`HTTP ${res.statusCode} ${url}`));
      }
      res.pipe(file);
      file.on('finish', () => {
        file.close(() => { fs.renameSync(tmp, dest); resolve(); });
      });
    }).on('error', (e) => { file.close(); reject(e); });
  });
}

/**
 * 把 dist_assets/images/ 整体铺到 www/assets/images/。
 *
 * 两边的相对结构刻意保持一致：
 *   dist_assets/images/00/09/xxx.webp  ->  www/assets/images/00/09/xxx.webp
 * 而 bio.db 里 image_path = "images/00/09/xxx.webp"（相对 output/，与 Python
 * 侧的 query.py / gui.py 共用同一份数据）。App 端用 IMG_BASE='assets/images/'
 * 加上剥掉 "images/" 前缀的路径取图。
 *
 * 剥前缀这一步在 build_assets.py 里做（rel_to_assets），这里只做原样复制，
 * 两边都剥会导致路径少一层，反而取不到图。
 */
function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  let n = 0;
  for (const ent of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, ent.name);
    const d = path.join(dst, ent.name);
    if (ent.isDirectory()) n += copyDir(s, d);
    else { fs.copyFileSync(s, d); n++; }
  }
  return n;
}

function dirSize(p) {
  let t = 0;
  const walk = (x) => {
    for (const ent of fs.readdirSync(x, { withFileTypes: true })) {
      const f = path.join(x, ent.name);
      if (ent.isDirectory()) walk(f);
      else t += fs.statSync(f).size;
    }
  };
  if (fs.existsSync(p)) walk(p);
  return t;
}

(async () => {
  fs.mkdirSync(WWW, { recursive: true });

  // ---- 1. sql.js ----
  console.log(`sql.js ${SQLJS_VERSION}`);
  for (const f of ['sql-wasm.js', 'sql-wasm.wasm']) {
    const dest = path.join(WWW, f);
    if (fs.existsSync(dest) && fs.statSync(dest).size > 1000) {
      console.log(`  ${f} 已存在，跳过`);
      continue;
    }
    process.stdout.write(`  下载 ${f} … `);
    await download(`${SQLJS_BASE}/${f}`, dest);
    console.log(human(fs.statSync(dest).size));
  }

  // ---- 2. 数据资产 ----
  if (skipDb) {
    console.log('--skip-db：跳过数据资产');
    return;
  }

  const dbSrc = path.join(DIST, 'bio.db');
  if (!fs.existsSync(dbSrc)) {
    console.error(`\n找不到 ${dbSrc}\n请先运行：python scripts/build_assets.py`);
    process.exit(1);
  }

  fs.mkdirSync(path.join(WWW, 'assets'), { recursive: true });

  const dbDst = path.join(WWW, 'assets', 'bio.db');
  fs.copyFileSync(dbSrc, dbDst);
  console.log(`bio.db -> www/assets/  ${human(fs.statSync(dbDst).size)}`);

  const imgSrc = path.join(DIST, 'images');
  if (!fs.existsSync(imgSrc)) {
    console.error(`找不到 ${imgSrc}`);
    process.exit(1);
  }
  const imgDst = path.join(WWW, 'assets', 'images');
  if (fs.existsSync(imgDst)) fs.rmSync(imgDst, { recursive: true, force: true });
  process.stdout.write('复制图片 … ');
  const n = copyDir(imgSrc, imgDst);
  console.log(`${n} 个文件, ${human(dirSize(imgDst))}`);

  const man = path.join(DIST, 'manifest.json');
  if (fs.existsSync(man)) fs.copyFileSync(man, path.join(WWW, 'assets', 'manifest.json'));

  console.log(`\nwww/ 总计 ${human(dirSize(WWW))}`);
})().catch((e) => { console.error('失败:', e.message); process.exit(1); });

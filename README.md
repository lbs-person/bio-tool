# bio-tool · 离线生物分类查询工具

一个完全离线可用的生物分类查询工具：把分类名录和物种图片打包到本地，用命令行或图形界面按学名、中文名和界门纲目科属检索，不需要联网。

- 仓库：<https://github.com/lbs-person/bio-tool>
- 本地路径：`D:\bio-tool`
- 数据本体是 SQLite 数据库（`output/taxa.db`）+ 本地图片目录（`output/images/`），没有服务端、没有外部 API 依赖。

> 项目状态：**未完成**。分类名录部分（156,107 行）是完整的，但图片覆盖率只有 **0.44%**。请先看完「当前进度与已知限制」再决定怎么用。

## 数据规模

| 文件 | 行数 / 数量 | 说明 |
| --- | --- | --- |
| `data/taxa.csv` | 156,107 行 | 全量分类名录，含亚种 |
| `data/taxa.csv` 物种级去重 | 143,020 个种 | 按 `species_sci` 去重 |
| `data/species_for_images.csv` | 143,020 行 | 种级去重后的抓图清单，额外带 `sci_name_for_image` 列（与 `species_sci` 完全一致） |
| `output/taxa.db` | 156,107 行 / 143,020 物种 | 由 `build_db.py` 生成的查询库 |
| 有图物种 | 626 / 143,020（0.44%） | 缺图 142,394 |
| `data/taxa.csv` 列 | 18 列 | `sci_name`, `cn_name`, `kingdom_latin`, `kingdom_cn`, `phylum_latin`, `phylum_cn`, `class_latin`, `class_cn`, `order_latin`, `order_cn`, `family_latin`, `family_cn`, `genus_latin`, `genus_cn`, `source`, `taxon_id`, `species_sci`, `is_subsp` |

图片本身：最长边 800px、WebP、quality 80，实测平均约 68.8 KB。

## 目录结构

```
bio-tool/
├─ data/
│  ├─ taxa.csv                 全量分类名录（含亚种），数据源见 source 列
│  └─ species_for_images.csv   种级去重后的抓图清单
├─ scripts/
│  ├─ fetch_images.py          从 iNaturalist 抓图 + 写版权元数据
│  ├─ verify_meta.py           校验图片与元数据是否一一对应（含 --migrate / --fix）
│  ├─ merge_meta.py            合并所有分片为 output/images_meta.csv
│  ├─ build_db.py              生成 output/taxa.db
│  ├─ query.py                 命令行查询（stats / search / info / export）
│  └─ gui.py                   图形界面查询（tkinter）
├─ output/                     生成物目录（大部分内容不进 git）
│  ├─ taxa.db                  SQLite 数据库（.gitignore）
│  ├─ images/                  图片，按 taxon_id 分两级目录（.gitignore）
│  ├─ images_meta_shNN_of10.csv  分片元数据，每个分片一份
│  └─ images_meta.csv          合并后的元数据（.gitignore）
└─ .github/workflows/fetch.yml GitHub Actions 抓图工作流
```

`data/` 是唯一需要长期版本管理的目录；`output/` 下的东西都能由脚本重新生成，或者从 artifact / Release 取回。

## 快速开始

### 环境依赖

- Python 3（工作流里用的是 3.11；本机验证用的是 3.12）
- 第三方包：`pandas`、`requests`、`Pillow`，抓图时还需要 `tqdm`
- `gui.py` 只用标准库 tkinter；没有 Pillow 时仍能浏览文字信息，只是不显示图片预览

```bash
pip install -r requirements.txt
```

依赖清单在 `requirements.txt` 里，共四个包：`pandas`、`requests`、`Pillow`、`tqdm`。GitHub Actions 的工作流也用这个文件安装，并据它计算 pip 缓存键。

### 从零跑通

```bash
# 抓图（本地或 Actions）
python scripts/fetch_images.py --shard 0 --shard-total 10 --count 2000

# 校验图片与元数据一致性
python scripts/verify_meta.py
python scripts/verify_meta.py --migrate --fix

# 合并元数据并建库
python scripts/merge_meta.py
python scripts/build_db.py

# 查询
python scripts/query.py stats
python scripts/query.py search 大熊猫
python scripts/query.py search 猫 --family 猫科
python scripts/query.py info "Panthera tigris"
python scripts/query.py export --with-image --out with_image.csv
python scripts/gui.py
```

如果图片目录不在默认位置（比如解压到了别处），`query.py` 和 `gui.py` 都支持 `--output`：

```bash
python scripts/query.py --output D:\bio-images\output stats
python scripts/gui.py --db output/taxa.db --output output
```

`image_path` 存的是**相对 `output/` 的路径**（形如 `images/00/08/00083415.webp`），所以 `--output` 指向的是 `output` 目录本身，不是 `output/images`。

只想建库和查询、暂时不要图片的话，`build_db.py` 在没有 `output/images_meta.csv` 时会正常生成一个不含图片信息的库，所有记录的 `has_image` 都是 0。

## 抓图流程说明

### 数据源与许可策略

- 图片源是 **iNaturalist**（`api.inaturalist.org`），不是 Wikipedia / Wikimedia Commons。
- 抓图分两步：先按学名查 taxon（`/v1/taxa`），再取该 taxon 下按投票数排序的观察记录里的照片（`/v1/observations`）。
- 只接受三种许可：**CC0、CC BY、CC BY-SA**（代码里的 `ALLOWED = {"cc0", "cc-by", "cc-by-sa"}`）。**不包含公有领域标记**——这不只是策略选择，也是实现事实：iNaturalist 的 `license_code` 只会是 `cc0` / `cc-by` / `cc-by-sa` / `cc-by-nc` 等这一套，其中 `cc-by-nc` 系列因为带 NC 限制也不接受。
- 同一个 taxon 会遍历多条观察记录、多张照片，返回**第一张**许可合规的照片；一张都没有就记为 `no_photo`。
- 请求限速：任意两次请求之间至少间隔 0.6 秒（`--sleep` 可调）。iNaturalist 未认证请求约 100 次/分钟，0.6 秒是留了余量的保守值。
- 学名带亚种（三名法）时，如果整名查不到 taxon，会退一步用二名法再查一次。

### 元数据与图片的强一致性

`fetch_images.py` 的核心约束是**元数据必须跟得上图片**：

1. 图片先写成 `.tmp`，用 PIL 重新打开并 `load()` 校验通过后，才 `os.replace` 原子改名到最终路径；
2. 只有上一步成功了，才写 `status=ok` 的元数据行，并 `flush` + `fsync`；
3. 任何一步失败都不留半成品文件，也不会留下「有记录没图片」的行。

每次运行开始时还会读回本分片已有元数据：`status=ok` 但磁盘上文件缺失的记录会被识别出来（打印警告）并重新抓取。

### 分片

- 分片方式是按物种在 `species_for_images.csv` 中的行号取模：`i % shard_total == shard`，每个物种只属于一个分片，不会重复。
- 分片元数据文件名形如 `images_meta_sh03_of10.csv`，**文件名里固化了分片总数**。这样以后改 `--shard-total` 不会和历史上的文件互相覆盖。
- 每个物种的图片路径是 `images/<taxon_id 前 2 位>/<第 3-4 位>/<taxon_id 补齐 8 位>.webp`，例如 taxon_id 为 83415 时是 `images/00/08/00083415.webp`。

### 时间预算与续跑

- `--max-minutes N` 给本次运行设时间上限，到点会打印提示并**正常收尾退出**（已抓的图片和元数据都已经落盘），而不是被 workflow 超时强杀导致整批结果丢失。
- 续跑是靠状态判断的：已经 `status=ok` 且图片仍在磁盘上的物种会被跳过，所以重复运行同一个分片只会继续抓没抓到的部分。
- 在 GitHub Actions 上，续跑还依赖 artifact 回灌（见下一节）。

### 耗时参考

本机实测（中国大陆网络直连 iNaturalist）约 **7 秒/物种**，远高于按限速推算的理论值 1.2 秒/物种，因为每次请求的实际往返延迟很高。按这个速度：

- 单分片 14,302 个物种需要约 28 小时，所以必须靠 `--max-minutes` 分多次运行；
- 一次 300 分钟的运行大约能处理 2,500 个物种。

在 GitHub Actions 的机器上（网络到 iNaturalist 快得多）会明显更快，实际吞吐以运行日志为准。

### 常用参数

```bash
python scripts/fetch_images.py --shard 0 --shard-total 10 --count 2000   # 常规抓图
python scripts/fetch_images.py --shard 0 --shard-total 10 --max-minutes 300
python scripts/fetch_images.py --shard 0 --shard-total 10 --limit 20     # 冒烟测试，只跑前 20 个物种
python scripts/fetch_images.py --shard 0 --shard-total 10 --verify-only  # 只校验本分片
```

参数：`--shard`、`--shard-total`、`--count`、`--sleep`、`--max-minutes`、`--limit`、`--verify-only`。

## 存储与分发

### 为什么图片不进 git

按平均约 68.8 KB 估算，143,020 个物种全量抓完约 **9.4 GB**，远超 GitHub 仓库的体积限制。所以：

- 仓库里只保留元数据 CSV、`output/taxa.db` 和脚本；
- `output/images/` 已加入 `.gitignore`；
- `output/taxa.db` 和 `output/images_meta.csv` 同样在 `.gitignore` 里（它们是生成物）。

### 通过 GitHub Actions 分发

`.github/workflows/fetch.yml` 是手动触发（`workflow_dispatch`）的工作流：

- 矩阵跑分片 0..9，每个分片一个 job，`fail-fast: false`；
- 每次运行先用 `actions/download-artifact` 按 `shard-<N>-*` 前缀取回本分片的历史产物到 `output/`，实现跨运行的断点续传；
- 取回后立刻跑一次分片级校验（`fetch_images.py --verify-only`），历史产物如果有脱节，在这里就会暴露，而不是等到最后；
- 抓完后用 `actions/upload-artifact` 把整个 `output/` 上传为 artifact。

注意事项：

- **artifact 有保留期**。当前工作流里写的是 `retention-days: 90`（公共仓库上限即 90 天）。以 artifacts 页面实际显示的过期时间为准，过期后会被删除，需要提前下载或转存。
- **长期分发建议走 Release 附件**。Release 附件不随 artifact 过期，适合当作归档。当前仓库里没有自动发布 Release 的 workflow，需要手动把 artifact 转成 Release 附件。
- 工作流默认输入：`count` 为 15000（已覆盖整个分片，等于不设限），`max_minutes` 为 300。也就是说实际节奏由时间预算决定：每次单分片跑 300 分钟后正常收尾，下次运行接着上次继续。job 超时 350 分钟，留出约 50 分钟给依赖安装、artifact 回灌与上传。
- 跨运行续跑**完全依赖 artifact 回灌**。如果 artifact 过期或被清理，本地又不保留图片，那部分进度就只能重抓。

### 把图片放回 output/ 使用

`taxa.db` 和 `images_meta.csv` 里记录的 `image_path` 都是相对 `output/` 的，所以只要把图片按原目录结构放回 `output/images/`，查询脚本和 GUI 就能找到：

1. 从 Actions 运行页面下载对应分片的 artifact，或者从 Release 下载压缩包；
2. 解压，让内容落到 `output/` 下（保证 `output/images/xx/yy/xxxxxxxx.webp` 这个层级结构不变）；
3. 回到仓库根目录跑 `python scripts/verify_meta.py`，确认图片与元数据一一对应且授权信息完整；
4. 跑 `python scripts/merge_meta.py && python scripts/build_db.py` 重新生成元数据合并结果和数据库。

如果不想动仓库目录，也可以解压到任意位置，再用 `--output <那个 output 目录>` 查询。

## 数据一致性与校验

`scripts/verify_meta.py` 是这个项目里最重要的「防事故」脚本，因为它检查的正是历史上真的出过问题的两件事。

**事故背景**：项目早期曾经出现 3,579 张图片里只有 626 张有元数据记录的情况，2,953 张图片成为**无法追溯来源与授权**的孤儿图。这些孤儿图随后被全部删除。同一时期，仓库根目录还误提交过 6 个 0 字节文件（`cd`、`dir`、`git`、`main`、`images`、`FETCH_HEAD`），已删除；`.gitignore` 里现在专门有规则阻止这类「shell 命令被误当成重定向目标」的文件再次出现。

现在 `verify_meta.py` 每次都会做三类检查：

1. **ok 记录指向的图片是否存在**——防止「有元数据没图片」；
2. **磁盘上是否存在没有元数据记录的图片**——防止孤儿图，也就是无法追溯授权的那种；
3. **每条 ok 记录是否带作者和许可证**——CC BY 系列要求署名，缺了就算问题。

两个层级的校验分工不同，别混用：

- `fetch_images.py --verify-only` 只校验**本分片**：本分片的 ok 记录有没有图、本分片物种对应的图片有没有缺失元数据。它不会因为别的分片的图片而误报，适合在 Actions 的单个 job 里跑。
- `verify_meta.py` 校验**全库**：把 `output/` 下所有分片合起来看，是全量校验的正确入口。

参数：

```bash
python scripts/verify_meta.py              # 只校验
python scripts/verify_meta.py --migrate    # 先把 images_meta_00.csv 这类历史命名迁到新格式，再校验
python scripts/verify_meta.py --migrate --fix   # 再额外删除孤儿图片
```

`--migrate` 会把历史文件 `images_meta_NN.csv` 改写成 `images_meta_shNN_of10.csv`、补齐缺失的列、给没有版本号的历史行标上 `schema_version=1`，并删掉过期的 `output/images_meta.csv`（让 `merge_meta.py` 重新生成）。`--fix` 会真正删除孤儿图片文件——删掉之后就无法恢复了，建议先不加 `--fix` 跑一遍看清楚输出。

抓图流程本身也在防同一类问题：`fetch_images.py` 每次收尾都会对自己那个分片跑一次校验，不通过就以非 0 状态退出。

## 当前进度与已知限制

这一节是如实记录，不粉饰。

- **图片覆盖率极低**：数据库 156,107 行 / 143,020 物种中，有图物种 **626 个，占 0.44%**，缺图 **142,394 个**。项目远未完成。
- **iNaturalist 抓取成功率约 15.6%**：累计已抓 4,000 条记录，其中 `status=ok` 仅 **626 条**，其余全部是没有找到合规授权照片的记录。这意味着即使把 143,020 个物种全部跑一遍，也会有**大量物种拿不到合规图片**——这不是漏抓，而是这些物种在 iNaturalist 上确实没有 CC0 / CC BY / CC BY-SA 的照片可用。
- **图片总量按覆盖率推算，全量约 9.4 GB**（按实测平均约 68.8 KB/张 × 143,020）。这只是按当前成功率的量级估算，实际取决于最终能抓到多少张。
- **异名匹配问题**：学名在 iNaturalist 上如果已被并入别的种，接口会返回接受名。脚本会记录 `matched_name` 和 `match_type`（`exact` / `synonym` / `none`），也就是说**这张图对应的可能是接受名那个物种，而不是原始学名对应的分类单元**。使用图片做物种级判断时要留意这一点。
- **当前分片状态**：`output/` 下实际存在 8 个分片文件（`sh00`、`sh01`、`sh03`、`sh04`、`sh05`、`sh07`、`sh08`、`sh09`），每片 500 行，合计 4,000 条记录、其中 `ok` 626 条。`sh02`、`sh06` 的元数据在切换到 iNaturalist 方案时被一并清空，尚未重新跑。
- **元数据格式落后于脚本**：现有 4,000 行元数据里，历史那 3,374 条未抓到的记录用的是 `status=no_image`（不是新脚本的 `no_photo`），`schema_version` 为 `1`，`match_type` 为空。两种写法 `merge_meta.py` 都能处理。新抓的行统一是 `schema_version=2`，不会再有这个差异。
- **图片许可分布（ok 记录，按物种）**：`cc-by` 471、`cc0` 95、`cc-by-sa` 60。

## 版权与许可

- 收集的图片**只接受 CC0、CC BY、CC BY-SA 三种许可**，逐张记录来源信息。
- **逐图署名**：CC BY 和 CC BY-SA 都要求署名。使用图片时必须按元数据里的 `author` 字段署名，并保留许可信息和来源链接。CC0 不强制署名，但元数据里同样记录了作者，建议一并保留。
- 图片元数据字段含义（这些字段同时存在于分片 CSV、`output/images_meta.csv` 和 `taxa.db` 里）：

| 字段 | 含义 |
| --- | --- |
| `author` | 作者 / 署名信息，取自 iNaturalist 照片的 attribution 字段 |
| `license` | 许可标识：`cc0` / `cc-by` / `cc-by-sa` |
| `license_url` | 对应的 Creative Commons 许可协议地址 |
| `page_url` | 图片所在的 iNaturalist 观察记录页面，用于溯源 |
| `image_path` | 图片相对 `output/` 的路径，空字符串表示这个物种没有合规图片 |
| `image_url` | 原始图片下载地址 |
| `image_bytes` / `image_w` / `image_h` | 落盘后的字节数、宽、高 |
| `status` | `ok` / `no_photo` / `no_taxon` / `download_failed`（历史数据里还有 `no_image`） |
| `matched_name` / `match_type` | iNaturalist 实际匹配到的名字，以及匹配类型 `exact` / `synonym` / `none` |
| `inat_taxon_id` | iNaturalist 的 taxon id |
| `schema_version` | 元数据格式版本，当前脚本写出的是 `2` |

- 分类名录数据本身的引用要求见下一节。

## 数据来源与引用

- **分类数据**来自文献资料，具体出处逐行记录在 `data/taxa.csv` 的 `source` 列里，例如「魏辅文,胡义波」「中国动物志 …」等文献 / 作者标注。使用这些分类数据时请按 `source` 列标注的来源引用。`query.py` 的 `search` / `info` 输出和 `gui.py` 的详情面板都会显示这一列。
- **图片数据**来自 iNaturalist 用户上传的照片，遵循各图自身的 CC 许可，引用方式见上一节；每张图都可以通过 `page_url` 回到原始观察记录。

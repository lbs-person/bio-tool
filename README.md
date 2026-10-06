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
├─ output/                     生成物与数据目录
│  ├─ taxa.db                  SQLite 数据库（.gitignore，可重新生成）
│  ├─ images/                  图片，按 taxon_id 分两级目录
│  ├─ images_meta_shNN_of10.csv  分片元数据，每个分片一份
│  └─ images_meta.csv          合并后的元数据（.gitignore，可重新生成）
└─ .github/workflows/fetch.yml GitHub Actions 抓图工作流
```

`data/` 与 `output/images_meta_shNN_of10.csv` 是需要长期版本管理的东西：前者是分类名录，后者是每张图来源与授权的唯一凭据。`taxa.db` 和 `images_meta.csv` 都能由脚本重新生成，所以不入库。

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

### 图片为什么改回随仓库走

这个决定推翻过一次，原因值得记下来，因为它是一个真实的坑。

最初的设计是把 `output/images/` 排除在 git 之外，图片只通过 Actions artifact 分发，理由是「按平均约 68.8 KB 估算全量会到 9.4 GB，超出 GitHub 仓库限制」。

但实际跑起来发现：**每次 Actions 运行的 runner 都是一次全新 checkout**。本地已有的那 626 张图既不在 git 里、也不在任何 artifact 里，于是分片抓完图做一致性校验时必然报错：

```
[错误] 80 条 ok 记录指向的图片不存在：
    images/00/00/00006550.webp
```

结果 10 个分片里有 8 个以 `exit 1` 收场。图片不在仓库里，就没法保证「元数据说有图、磁盘上真有图」这个不变式。

现在改成图片随仓库走：

- `output/images/` 正常入库，只有 `*.tmp` 这类临时文件排除；
- `output/taxa.db` 和 `output/images_meta.csv` 仍然在 `.gitignore` 里（它们能由脚本重新生成）；
- `output/images_meta_shNN_of10.csv` 必须入库——它是每张图来源、作者与授权的唯一凭据。

顺带说明：本项目定位是本地离线自用，不做公开分发，加上 iNaturalist 的实际覆盖率有限（见「当前进度与已知限制」），全量也不会真的到 9.4 GB。

### GitHub Actions 工作流

`.github/workflows/fetch.yml` 是手动触发（`workflow_dispatch`）的工作流：

- 矩阵跑分片 0..9，每个分片一个 job，`fail-fast: false`；
- 每次运行先用 `actions/download-artifact` 取回本分片的历史产物到 `output/`，实现跨运行的断点续传；
- 取回后立刻跑一次分片级校验（`fetch_images.py --verify-only`），历史产物如果有脱节，在这里就会暴露，而不是等到最后；
- 抓完后用 `actions/upload-artifact` 把整个 `output/` 上传为 artifact。

注意事项：

- 续跑同时依赖**两处状态**：仓库里的 `output/images_meta_shNN_of10.csv`（checkout 自带）和 Actions artifact。仓库那份是主，artifact 是补充，所以别在没提交元数据的情况下指望纯靠 artifact 续跑。
- **artifact 有保留期**。当前工作流里写的是 `retention-days: 90`（公共仓库上限即 90 天）。过期后会被删除，此时靠仓库里的元数据 + 图片仍然能续跑，只是可能重抓一部分。
- 工作流默认输入：`count` 为 15000（已覆盖整个分片，等于不设限），`max_minutes` 为 300。实际节奏由时间预算决定：每次单分片跑完后正常收尾，下次运行接着上次继续。job 超时 350 分钟，留出约 50 分钟给依赖安装、artifact 回灌与上传。
- 实测参考：一个分片跑满 14,302 个物种约需 **186 分钟**（约 1.3 个物种/秒），所以默认的 300 分钟预算能跑完整个分片，一次运行即可收敛。

### 通过 pull_artifacts.py 把结果拿回本地

Actions 不会把结果推回仓库，所以要主动下载（或者直接从 git 拉已提交的元数据与图片）：

```bash
# 需要 token：https://github.com/settings/tokens（经典 token 勾 repo）
set GITHUB_TOKEN=ghp_xxx
python scripts/pull_artifacts.py --dry-run     # 先看会下载什么
python scripts/pull_artifacts.py               # 下载并合并进 output/
```

这个脚本会跳过两类无用产物：更早的 20 分片方案遗留（`shard-N`，会覆盖新数据）和失败运行留下的空产物（约 100 KB）。同名元数据 CSV 按**行数**决定保留哪一份，行数多的赢。

### 校验与重建数据库

图片现在随仓库走，所以正常 `git pull` 之后 `output/images/` 就已经是对的了。抓完图后按顺序跑：

```bash
python scripts/verify_meta.py        # 先看图片与元数据是否一一对应
python scripts/merge_meta.py         # 合并分片元数据
python scripts/build_db.py           # 重建 taxa.db
```

如果用的是 artifact 而非 git（比如换台机器、或 git 里还没有那些图），就用 `pull_artifacts.py` 把产物合并进 `output/`，再跑上面三行。

`image_path` 存的是相对 `output/` 的路径，所以图片必须保持在 `output/images/xx/yy/xxxxxxxx.webp` 这个层级。想放到别处也行，查询时用 `--output <那个 output 目录>`。

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

- **图片覆盖率仍然很低**：数据库 156,107 行 / 143,020 物种中，有图物种 **6,760 个，占 4.73%**，缺图 **136,260 个**。项目远未完成。
- **一轮完整抓取的真实结果**：10 个分片跑满全量 143,020 个物种，得到 `ok` 6,760 条、`no_taxon` 约 11,000/片、`no_photo` 约 2,600/片。折合成功率约 **4.7%**——也就是说**把全量跑一遍，能配上图的只有约 6,760 个物种**，而且再跑一遍也不会变多（失败状态会被记录、不会重复重试）。
- **iNaturalist 对这批名录的覆盖率是硬限制**。随机抽样 40 个物种直接查 iNaturalist API，**37.5% 完全查不到任何记录**；再加上「有记录但没有 CC0/CC BY/CC BY-SA 照片」的部分，最终只有 4.7% 能配上图。要提高覆盖率只能引入别的数据源（Wikimedia Commons、GBIF、EOL 等），当前脚本不支持。
- **仓库体积会随抓图增长**。实测平均约 69.3 KB/张。按不同覆盖率推算全量规模：

  | 假设 | 图片数 | 体积 |
  | --- | --- | --- |
  | 当前实测覆盖率 4.73% | 6,760 | **约 0.47 GB** |
  | 旧方案实测成功率 15.6% | 22,311 | **1.48 GB** |
  | 假设全部有图 | 143,020 | 9.46 GB |

  图片现在随仓库走（原因见「存储与分发」），当前仓库约 **0.5 GB** 量级。GitHub 推荐仓库体积 1 GB 以内、硬上限 5 GB；按当前覆盖率不会撞限。如果真的长到影响 clone 速度，可以把 `output/images/` 重新加回 `.gitignore`，改为只提交元数据与数据库、图片走 artifact（代价就是前文说的「新 checkout 缺图导致校验失败」，需要额外机制配合）。
- **异名匹配问题**：学名在 iNaturalist 上如果已被并入别的种，接口会返回接受名。脚本会记录 `matched_name` 和 `match_type`（`exact` / `synonym` / `none`），也就是说**这张图对应的可能是接受名那个物种，而不是原始学名对应的分类单元**。使用图片做物种级判断时要留意这一点。
- **分片状态**：10 个分片文件齐全（`sh00` ~ `sh09`），每片 14,802 行（500 行历史 + 14,302 行新抓），全量 143,020 个物种已覆盖一遍。
- **历史格式遗留**：每片里那 500 行早期记录仍是 `schema_version=1` / `status=no_image`（旧写法）；新抓的 14,302 行统一是 `schema_version=2` / `status=no_photo`。两种写法 `merge_meta.py` 都能处理，不影响使用。
- **图片许可分布（按物种）**：`cc-by` 5,046、`cc0` 1,094、`cc-by-sa` 620。

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

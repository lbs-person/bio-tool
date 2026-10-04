# Bio Taxonomy Offline

离线生物分类查询工具。数据来源：Wikipedia / Wikimedia Commons。

## 数据规模

- 物种总行数：156,107
- 种级去重：143,020
- 图片：每物种 1 张，800px WebP

## 版权

所有图片均来自 Wikimedia Commons，仅保留 CC0 / 公有领域 / CC BY / CC BY-SA 授权的图片。
每张图的作者、许可证、来源见 `output/images_meta.csv`。

## 数据文件

- `data/taxa.csv`：所有物种（含亚种）
- `data/species_for_images.csv`：种级去重后用于抓图的列表
- `output/taxa.db`：SQLite 数据库
- `output/images/`：图片
- `output/images_meta.csv`：图片版权信息

## 抓图

在 GitHub Actions 页面手动触发，每次跑 3000 张，约 1.5 小时。
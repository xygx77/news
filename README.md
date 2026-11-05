# 每日简报（Daily Briefing）

每天北京时间 08:10 自动生成两份 A4 HTML 简报与一个归档首页：

- **开盘前晨报**：A 股、港股、美股、欧股、日韩、利率汇率、大宗商品、财经日历、风险提示。
- **激光行业简报**：全球激光产业链的企业动态、产品发布、融资并购、技术突破、标准规范，每天 5–8 条。
- **首页**：两份报告入口 + 按月份归档。

站点：<https://xygx77.github.io/news/daily/>（GitHub Pages，`main` 分支 `/docs`）。

## 目录结构

```
daily-engine/          # 自包含的生成器代码
  src/daily/           # 采集、行情、规则写作、渲染、CLI
  templates/daily/     # 晨报、激光简报、首页模板
  data/                # 配置、关键词、节假日表
  tests/daily/         # 离线 fixtures 与测试
  pyproject.toml
  uv.lock
docs/daily/            # 生成物（GitHub Pages 发布目录）
  index.html
  latest/
  YYYY-MM-DD/
  run.log
```

## 本地运行

在 Horizon 仓库根目录或 `daily-engine/` 目录：

```bash
uv sync --extra dev
uv run horizon-daily --config data/config.daily.json --out output/daily
```

离线验证（不访问网络）：

```bash
uv run horizon-daily --config data/config.daily.json --out output/daily \
  --offline --fixtures-dir tests/daily/fixtures --date 2026-11-05
```

Windows 计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1
```

任务名 `Horizon Daily Briefing`，每天 08:10 运行，开机补跑。日志在 `output/daily/run.log`。

## 云端部署

前置条件：

- 仓库 `xygx77/news` 公开，或账号支持私有仓库 GitHub Pages。
- `main` 分支允许 GitHub Actions 直接推送（不能有「必须 PR / 必须 review」的保护规则）。
- Settings → Pages → Source 选择 **Deploy from a branch**，分支 `main`，目录 `/docs`。

同步代码与工作流：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sync_news_repo.ps1 -Push
```

脚本会把生成器复制到 `daily-engine/`，把 `scripts/daily.yml` 写到 `.github/workflows/daily.yml`，并创建 `docs/daily/` 与 `docs/.nojekyll`。之后每天 00:10 UTC（08:10 北京时间）自动运行；也可在 Actions 页面手动触发 `Daily Briefing`。

## 数据口径

- 所有数字来自公开接口，表格与正文均标注来源链接。
- 取不到的数据写「暂缺」，不用近似值或旧数据替代。
- 页面只使用内联 CSS/JS 与相对链接，不加载外部 CDN 或字体。
- `run.log` 会随 `docs/daily/` 提交并公开，只包含源名、条数、耗时和错误摘要，不包含任何凭据。

## 规则写作说明

v1 不调用大模型：晨报由事实表 + 固定句式生成，激光简报按关键词权重排序并摘录正文。趋势点评位置保留，接入 AI 后再启用。

# 飞序 Flowing 品牌素材

飞序 Flowing 是面向飞书的自然语言工作台。品牌图形用折叠的终端提示符 `>` 表达指令执行和工作流连接；界面文字使用网页字体，避免把小字号文字固化在图片里。README 主视觉采用深海蓝 + 青绿色流线，以黏土风小人物协作工坊表达从一句话到任务完成的过程。

## 视觉规范

| 色彩 | 色值 | 用途 |
| --- | --- | --- |
| 深蓝 | `#173B55` | 品牌名称、主体轮廓 |
| 青绿 | `#24B7A5` | 指令流、连接和强调 |
| 浅底 | `#F7F8FA` | 图形底板、预览背景 |

图形是带浅色底板的 PNG，不是透明图或可编辑矢量。请保留宽高比例；深色背景继续使用浅色底板。小尺寸场景只使用图形，不附细小英文。

## 素材清单

素材位于 `frontend/public/brand/`，启动网站后访问 `/brand/index.html` 查看组合与下载。

- `logo-master.png`：1254 × 1254 的 Logo 原图。
- `cover-flowing.png`：1536 × 1024 的浅色品牌封面，可用于项目介绍和社交预览。
- `docs/assets/flowing-hero.png`（仓库根目录相对路径）：1672 × 941 的 AI 生成 README 主视觉，小人物协作处理任务，流线串起对话、确认、文档、日历和表格。
- `icon-{16,32,64,180,192,512}.png`：浏览器、界面、Apple Touch 和应用图标。
- `index.html`：独立品牌预览页，可直接在浏览器打开。
- `../site.webmanifest`：应用名称、主题色和图标配置；不代表已提供离线功能。

`BrandMark.vue` 统一界面图形。相邻文字提供品牌名称，图片本身作为装饰，避免屏幕阅读器重复朗读。

README 主视觉使用仓库内的 PNG，不依赖外部图片服务，可在 GitHub、离线镜像和文档站点展示。图片只保留 Flowing 品牌名称，产品说明由 README 正文承载。

## README 动效与主视觉

执行链图由内置 imagegen 生成，使用低饱和蓝、暖珊瑚和青绿区分理解取证、确认执行与结果记忆，并以人物确认、工具检索和归档插画表达各环节。README 引用 `assets/execution-chain-ai.png`；提示词保存在 `assets/execution-chain-ai.prompt.md`。此前的 SVG、PDF 和 draw.io 版本仅作为历史设计素材保留，不是当前 AI 图片的可编辑源文件。

README 主视觉使用 `assets/flowing-hero.png`，由内置 imagegen 生成。生成提示词见 [flowing-hero.prompt.md](assets/flowing-hero.prompt.md)。

`assets/flowing-workflow.gif` 保留为执行链动态概念演示素材，当前 README 未引用；`assets/flowing-workflow.png` 是 GIF 的静态版本。

动画由 `scripts/render_readme_animation.py` 生成，只依赖 Pillow，不请求远程服务：

```sh
backend/.venv/bin/python scripts/render_readme_animation.py
```

脚本支持 `--font`、`--bold-font`、`--width`、`--height` 和 `--frames` 参数。默认会查找 macOS Arial 或 Linux DejaVu Sans；生成结果写入 `docs/assets/`。

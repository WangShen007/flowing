# 飞序 Flowing 品牌素材

飞序 Flowing 是面向飞书的自然语言工作台。品牌图形用折叠的终端提示符 `>` 表达指令执行和工作流连接；界面文字使用网页字体，避免把小字号文字固化在图片里。README 的展示层采用深海蓝 + 青绿色流线，把 Web、微信、Telegram 三个入口和同一个执行器放在一张图中。

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
- `assets/flowing-hero.svg`：1600 × 900 的 README 主视觉，展示三种入口、LangGraph 执行链和飞书能力面。
- `icon-{16,32,64,180,192,512}.png`：浏览器、界面、Apple Touch 和应用图标。
- `index.html`：独立品牌预览页，可直接在浏览器打开。
- `../site.webmanifest`：应用名称、主题色和图标配置；不代表已提供离线功能。

`BrandMark.vue` 统一界面图形。相邻文字提供品牌名称，图片本身作为装饰，避免屏幕阅读器重复朗读。

README 主视觉使用原生 SVG，不依赖外部图片服务；它可以在 GitHub、离线镜像和文档站点保持清晰缩放。中文文案只保留产品边界和执行状态，避免把无法验证的营销数字写进图片。

## README 动效与主视觉

README 主视觉使用 `assets/flowing-hero.svg`，展示 Web、微信、Telegram 三种入口如何汇入同一个执行器；下方仍保留 `flowing-workflow.gif` 作为执行链的动态概念演示。两者都不是实际业务执行录屏；`flowing-workflow.png` 是 GIF 的静态版本，便于不支持动画的环境查看。

动画由 `scripts/render_readme_animation.py` 生成，只依赖 Pillow，不请求远程服务：

```sh
backend/.venv/bin/python scripts/render_readme_animation.py
```

脚本支持 `--font`、`--bold-font`、`--width`、`--height` 和 `--frames` 参数。默认会查找 macOS Arial 或 Linux DejaVu Sans；生成结果写入 `docs/assets/`。

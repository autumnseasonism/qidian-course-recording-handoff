# 视频页面扫描与导出

本包自带 `scripts/scan_pages.py` 和 `scripts/export_pages.py`，不依赖其他课程 Skill、项目专用脚本或旧会话变量。需要本地 Python 3.9+、Pillow、numpy、reportlab、pypdf、python-pptx，以及显式指定的 FFmpeg 和 FFprobe。扫描当前要求 FFmpeg 9+，只接受经全帧时间戳核验的恒定帧率视频；不支持的版本或变帧率会明确失败，不静默降低标准。工具不安装依赖、不调用远程服务。

## 1. 扫描候选

所有路径参数使用绝对路径。`workspace-root` 是获准读写的工作目录；输入视频、输出必须严格位于其下。拒绝链接、Windows junction/reparse point、越界、已有输出目录。参数示例中的路径需替换为当前真实路径：

```text
python scripts/scan_pages.py --workspace-root ABS_ROOT --input ABS_VIDEO --output-dir ABS_NEW_CANDIDATES --ffmpeg ABS_FFMPEG --ffprobe ABS_FFPROBE --lesson-id LESSON_ID
```

扫描在读取前固定视频身份、完整 SHA256、大小，探测实际时间戳，再全帧解码。按实际帧编号保留变化帧、变化前后帧、每 4 秒候选及末帧，完整帧 PNG 不裁剪。低分辨率分析只用于发现候选。源视频读取后和索引发布前后重新核验，不把替换后的源当成原始证据。运行失败保留诊断；仅产生候选，不保证覆盖所有动画或课件页。

结果包含 `候选索引.json`、`候选原图/`、候选总览 PNG、全帧与导出日志。索引中的 `actual_seconds` 是实际源视频时间；2 倍速成片的时间就是成片时间，不自动乘 2 伪装成原课程时间。需要并列课程时间时另保留显式映射。

## 2. 明确选择正式页面

Agent 必须查看候选原图和必要相邻帧，保留课件正文、整页比例、水印及不同动画状态。相同页面再次出现可按实际顺序保留；不能因为标题相同就自动合并。准备一个新的 UTF-8 JSON 选择文件：

```json
{
  "candidate_index_sha256": "候选索引文件实际 SHA256，小写 64 位",
  "source_sha256": "候选 signature.input_sha256",
  "review": {"reviewer": "审阅者", "scope": "实际看过的范围", "limitations": "仍待核验部分"},
  "pages": [
    {"title": "明确页面标题", "first_candidate": 1, "representative_candidate": 2, "last_candidate": 3, "animation_note": "实际动画疑点或检查结果"}
  ]
}
```

三项候选 ID 引用候选索引的 `index`，不能填近似秒数或虚构 ID。允许单帧状态的三项相同；边界是选中实际帧的观察时间，不推断连续页面持续区间。各页按时间排列，区间不交叠，代表帧时间递增。选择文件 SHA 与候选索引 SHA 一并进入结果。`review` 只作为审阅者声明保留，工具不将其当作人审通过证据。

## 3. 导出与机检

```text
python scripts/export_pages.py --workspace-root ABS_ROOT --candidates ABS_CANDIDATE_INDEX --selection ABS_SELECTION --output-dir ABS_NEW_EXPORT --ffprobe ABS_FFPROBE --font-file ABS_INSTALLED_TTF_OR_TTC
```

导出前检查视频实际大小、完整 SHA、时长、画面尺寸、候选索引及选择绑定关系、页面顺序、图片 SHA/尺寸，全部输入检查通过后才创建新目录。候选目录存在 `候选索引发布失败.json` 或 `FAILED.json` 时拒绝导出。中文等非 ASCII 标题必须显式提供本机已经安装、可嵌入并覆盖标题字形的 TTF/TTC 字体；字体文件作为只读输入核验 SHA，工具不安装或复制字体。字体不可用、缺字或完整标题无法在五行可读页脚中放下时，输出前明确拒绝，不能截断或加略号。

输出为：

- `pages/0001.png` 等：原候选 PNG 原字节复制，整页无裁剪。
- `page-index.json`、`pages.csv`：页序、标题、来源帧、首/代表/末时间、动画说明、哈希。
- `pages.pdf`：每页一张整帧图片，完整页面标题和时间码放在图外下方。实际检查 PDF 页数及每页嵌入图的解码 RGB 像素哈希。
- `pages.pptx`：每页一张整帧图片，时间码放在图外下方，完整标题存入该页演讲者备注以免挤压图片。重新打开并检查实际页数、每页原 PNG 字节 SHA 与完整标题备注。

`page-index.json` 的 `outputs` 字典记录相对于索引父目录的所有 PNG、PDF、PPT、CSV 文件及完整 SHA，可由 `check_archive.py --verify-hashes` 复查。选择与生成结构经过机检仍属于 `needs_review`，不能未经人工验收登记 `completed`。

导出期间再次检查所有源文件身份和完整哈希；失败留下 `FAILED.json` 和诊断产物。**存在 `FAILED.json` 的输出目录始终视为失败，即使存在索引文件。** 重试必须使用新目录，不覆盖旧产物。

成功机检状态是 `machine_checked_human_review_pending`；`human_review_verified=false`、`completeness_verified=false`、`rendering_status=not_rendered`。页数和图片哈希一致只能证明生成结构与输入一致，不能证明页面齐全、文字可读或渲染正常。包内 `scripts/render_pages.py` 提供显式渲染入口，外部渲染器不随包分发：PDF 需要 `pdftoppm`，PPT 另需要 `soffice`。未安装或不能正常执行时不宣称渲染成功，不安装软件；缺失应报告而不是用原 PNG 冒充 PPT 渲染结果。

```text
python scripts/render_pages.py --workspace-root ABS_ROOT --input ABS_PDF --output-dir ABS_NEW_PREVIEWS --pdftoppm ABS_PDFTOPPM --expected-pages PAGE_COUNT
python scripts/render_pages.py --workspace-root ABS_ROOT --input ABS_PPTX --output-dir ABS_NEW_PREVIEWS --pdftoppm ABS_PDFTOPPM --soffice ABS_SOFFICE --expected-pages PAGE_COUNT
```

逐页打开实际渲染预览检查文字、比例、时间码、水印和动画保留范围。渲染失败须保留退出证据，不把 ZIP/结构检查当成渲染成功。渲染和视检结果单独记录，不自动改成已验收。

扫描阈值可能漏掉小变化、低对比度变化或短暂动画；底部变化更依赖定时帧与人工检查。导出保持图像比例、水印与原像素，不从截图生成可编辑的正文课件，也不宣称获得网站原始课件。

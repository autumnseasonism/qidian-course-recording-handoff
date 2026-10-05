# 配套资料本地验收

`--root` 必须是实际普通目录的绝对路径；工具先用 `lstat` 检查提交路径自身及词法祖先，再解析路径，拒绝根目录或祖先的 junction/符号链接。文件路径检查同样包括断链，不通过 `exists()` 跳过断链；不接受相对根目录。

`scripts/audit_materials.py` 只检查已经通过网站正常下载或保存的文件，不请求网址、不解压、不绕过保护。Python 3.9+，仅标准库。它不会把文件存在或容器完整当成课程内容已验收。

输入 JSON 是对象，含 `items` 数组；每项明确 `source`（正常下载页/资料来源，不含密码或令牌）、`lesson`、`title`、`status`，保存成功项另有 `path`。状态只接受 `downloaded`、`saved`、`failed`、`pending`。可提供预期 `sha256`、失败 `reason`。示例：

```json
{"items":[{"source":"https://example.org/course/materials","lesson":"M01-001","title":"课件","status":"downloaded","path":"materials/01.pdf"},{"source":"https://example.org/course/materials","lesson":"M01-002","title":"附件","status":"pending","reason":"网站未提供下载入口"}]}
```

运行时显式提供授权根目录、清单和全新报告路径，路径可以相对于根目录；所有文件必须在根目录内，报告父目录须存在，已有报告拒绝覆盖，链接及 Windows 重解析路径拒绝。参数示例：

```text
python scripts/audit_materials.py --root <归档根目录> --manifest materials.json --output evidence/material-audit-01.json
```

工具检查文件存在、非空、SHA256，记录原件检查前后哈希一致。ZIP 和 Office ZIP 容器逐项检查路径、链接/特殊文件、加密、条目数量、解压后体积、压缩比；在受限流读取中检查 CRC，不向磁盘解压。默认上限为 10000 项、单项 256 MiB、合计 1 GiB、压缩比 200，可通过命令行的 `--max-zip-*` 参数合理调整；超限只报失败，应先人工核实，不能为绕过可疑文件盲目调高。

HTML 头部或 HTML 扩展名的文件若包含登录、权限或错误提示词，标记 `needs_review`，可能存在正常正文误报，也可能漏掉不在头部的提示。保存失败和待补项原样保留。通过项仍为 `local_integrity_passed_content_review_pending`；之后应实际打开 PDF、图片、正文等，核对课节/标题/页数和资料目录数量，另记内容验收。工具不渲染 PDF/图片、不检查所有内容、不证明下载完整性，`all_materials_completed` 始终为 false。

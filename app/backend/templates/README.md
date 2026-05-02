# Backend templates

## reference.docx

Pandoc 通过 `--reference-doc=reference.docx` 把这个 .docx 当作样式蓝本(标题层级、段落字号、表格边框、字体)。运行时由 `services/docx_export.py` 引用,路径来自 `settings.templates_dir`(默认 `/app/backend/templates`)。

当前的 `reference.docx` 是**最小可用占位**:
- `_rels/.rels` + `word/_rels/document.xml.rels` + 空 `word/document.xml`
- `word/styles.xml`:
  - 默认字体 `Noto Sans CJK SC`(Dockerfile 装的 `fonts-noto-cjk` 包)
  - `Heading 1`-`Heading 4` 字号 36 / 30 / 26 / 24,加粗
  - `Normal` 字号 22

足够让 Pandoc 输出的章节有清晰的层级,且中文不乱码。

## 真手作版本(M3 Day1 / M5 验收)

更完整的 `reference.docx`(带封面、表格边框、页眉页脚)需要用 LibreOffice / Word 手工编辑。建议步骤:

1. 用 LibreOffice 打开当前的 `reference.docx`
2. 修改 `Heading 1`-`Heading 4` 样式(右键 → 编辑段落样式),按公司视觉标准调
3. 拉一个 1×1 的表格,设置外边框 + 内部网格线 → 删除表格,但样式保留在文档里
4. 调段落间距、行距(Normal 样式)
5. **不要**写实际内容到 `<w:body>` 里(reference doc 只用来取样式,正文会被覆盖)
6. 保存覆盖此文件

## 重新生成最小占位

```bash
cd app/backend
python3 -c '
import zipfile
# (粘贴本目录历史 commit 中 templates/README.md 引用的 generate-reference 脚本,
#  或直接看 git log 找到对应 commit 的 inline python 调用)
'
```

也可以直接用 `pandoc -o reference.docx --print-default-data-file reference.docx` 拿 Pandoc 默认样式版做起点。

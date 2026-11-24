"""把 Markdown 中的 drawio 架构图配置块渲染为 PNG 图片引用。

用法：在导出 docx 前调用 ``expand_drawio_blocks(md, temp_dir)``。
配置块形如::

    ```drawio
    {"title": "...", "layers": [...], "sidebars": [...]}
    ```

块内容会交给 ``tools.drawio_builder.build_from_config`` 同时生成 .drawio 与 .png，
随后被替换为 ``![](PNG绝对路径)``，交给现有的 pypandoc 链路嵌入 docx。
单个块生成失败时保留错误提示，不影响整本标书导出。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from tools.drawio_builder import build_from_config
except ImportError:  # 作为脚本直接运行时
    from drawio_builder import build_from_config

logger = logging.getLogger(__name__)

# 允许代码块标记带空格，内容用 JSON；兼容 CRLF。
BLOCK_RE = re.compile(r"```drawio[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL)


def render_drawio_png_base64(raw: str) -> Optional[str]:
    """把单个 drawio 配置渲染为 PNG 的 base64 data URI；失败返回 None。

    HTML 预览直接用 ``<img src="data:image/png;base64,...">`` 内嵌，无需落盘到
    static/data 目录，也就没有过期图片和 URL 路径问题。
    """
    try:
        config: Dict[str, Any] = json.loads(raw.strip())
        with tempfile.TemporaryDirectory(prefix="drawio_html_") as tmp:
            stem = Path(tmp) / "diagram"
            drawio_file = stem.with_suffix(".drawio")
            png_file = stem.with_suffix(".png")
            build_from_config(config, str(drawio_file), str(png_file))
            encoded = base64.b64encode(png_file.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception as exc:  # noqa: BLE001 - 单块失败不阻断整页预览
        logger.error("drawio HTML 预览渲染失败: %s", exc)
        return None


def expand_drawio_blocks(md: str, temp_dir: str | Path) -> str:
    """扫描 md 中的 drawio 配置块，生成 PNG 并替换为 Markdown 图片引用。"""
    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    def replace(match: re.Match) -> str:
        raw = match.group(1).strip()
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
        stem = temp_path / f"drawio_{digest}"
        drawio_file = stem.with_suffix(".drawio")
        png_file = stem.with_suffix(".png")
        try:
            config: Dict[str, Any] = json.loads(raw)
            build_from_config(config, str(drawio_file), str(png_file))
            logger.info("drawio 架构图已生成: %s", png_file)
            return f"![]({png_file.as_posix()})\n"
        except Exception as exc:  # noqa: BLE001 - 单块失败不阻断整本导出
            logger.error("drawio 架构图生成失败: %s", exc)
            return f"> [架构图生成失败：{exc}]\n"

    return BLOCK_RE.sub(replace, md)


if __name__ == "__main__":
    sample = """
# 测试

```drawio
{"title":"测试架构","layers":[{"name":"上层","modules":["A","B"]},{"name":"下层","modules":["C"]}],"sidebars":[{"name":"保障","side":"left"}]}
```
"""
    import sys
    import tempfile, shutil

    with tempfile.TemporaryDirectory(delete=False) as d:
        out = expand_drawio_blocks(sample, d)
        print(out)
        print("生成文件:", list(Path(d).iterdir()))

    print(f"临时目录保留在: {d}")
    input("查看完毕后按回车删除...")
    shutil.rmtree(d, ignore_errors=True)    

"""Builder for draw.io (.drawio) architecture diagrams and matching PNG previews.

XML generation uses only the standard library. PNG rendering uses Pillow and
reuses the exact same geometry captured for the draw.io cells, so the two
outputs stay pixel-consistent. Callers that only need ``.drawio`` never have
to install Pillow.

Typical usage::

    from drawio_builder import build_from_config

    build_from_config(config_dict, "output/diagram.drawio", "output/diagram.png")
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #

PRESETS: Dict[str, Dict[str, Any]] = {
    "layered_architecture": {
        "page_width": 1600,
        "page_height": 1000,
        "title_height": 46,
        "title_font_size": 26,
        "layer_height": 140,
        "layer_gap": 26,
        "layer_margin_x": 110,
        "layer_header_height": 46,
        "module_padding_x": 30,
        "module_gap": 14,
        "module_bottom_padding": 16,
        "sidebar_width": 72,
        "sidebar_margin_x": 18,
        "sidebar_font_size": 20,
        "arrow_fractions": [0.2, 0.5, 0.8],
        "arrow_color": "#78909C",
        "arrow_width": 2,
        "png_scale": 2,
        "png_background": "#FFFFFF",
        "layer_colors": [
            {"fill": "#E3F2FD", "border": "#1565C0", "text": "#0D47A1"},
            {"fill": "#E8EAF6", "border": "#3949AB", "text": "#1A237E"},
            {"fill": "#E0F7FA", "border": "#00838F", "text": "#006064"},
            {"fill": "#E8F5E9", "border": "#2E7D32", "text": "#1B5E20"},
            {"fill": "#F5F5F5", "border": "#616161", "text": "#424242"},
        ],
        "sidebar_colors": {
            "fill": "#37474F",
            "border": "#263238",
            "text": "#FFFFFF",
        },
    }
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _short_id() -> str:
    """Return a short, draw.io compatible cell id."""
    return uuid.uuid4().hex[:12]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *override* into *base* and return a new dict."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _vertical_label(text: str) -> str:
    """Render a label as one character per line for narrow vertical bars."""
    return "<br>".join(list(text))


def _xml_text(value: str) -> str:
    """Escape a value for use in an XML attribute / element text."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #

class DrawioBuilder:
    """Accumulate draw.io cells and serialize them to an ``.drawio`` file."""

    def __init__(self, title: str, page_width: int = 1600, page_height: int = 1000) -> None:
        self.title = title
        self.page_width = page_width
        self.page_height = page_height
        self._vertices: List[Dict[str, Any]] = []
        self._edges: List[Dict[str, Any]] = []

    # -- cell creation ------------------------------------------------------ #

    def add_vertex(
        self,
        value: str,
        style: str,
        x: float,
        y: float,
        width: float,
        height: float,
        render: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a rectangular / styled vertex and return its cell id.

        ``render`` carries PNG-only hints (kind, colors, font size, radius,
        alignment). It never affects the draw.io XML.
        """
        cell_id = _short_id()
        self._vertices.append(
            {
                "id": cell_id,
                "value": value,
                "style": style,
                "x": round(x, 2),
                "y": round(y, 2),
                "width": round(width, 2),
                "height": round(height, 2),
                "render": render or {},
            }
        )
        return cell_id

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        style: str,
        value: str = "",
        render: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add an edge between two vertices and return its cell id."""
        cell_id = _short_id()
        self._edges.append(
            {
                "id": cell_id,
                "value": value,
                "style": style,
                "source": source_id,
                "target": target_id,
                "render": render or {},
            }
        )
        return cell_id

    # -- high level helpers ------------------------------------------------ #

    def add_title(self, preset: Dict[str, Any]) -> str:
        """Add the diagram title text."""
        return self.add_vertex(
            value=self.title,
            style=(
                "text;html=1;strokeColor=none;fillColor=none;"
                f"align=center;verticalAlign=middle;fontSize={preset['title_font_size']};"
                "fontStyle=1;fontColor=#1F4E79;"
            ),
            x=0,
            y=8,
            width=self.page_width,
            height=preset["title_height"],
            render={
                "kind": "text",
                "color": "#1F4E79",
                "font_size": preset["title_font_size"],
                "bold": True,
                "align": "center",
                "valign": "middle",
            },
        )

    def add_layer(
        self,
        name: str,
        modules: Sequence[str],
        x: float,
        y: float,
        width: float,
        height: float,
        colors: Dict[str, str],
        preset: Dict[str, Any],
    ) -> str:
        """Add one layer band with a title and evenly spaced module boxes.

        Returns the id of the layer band so that inter-layer arrows can
        reference it.
        """
        layer_id = self.add_vertex(
            value=name,
            style=(
                "rounded=1;arcSize=5;whiteSpace=wrap;html=1;"
                f"fillColor={colors['fill']};strokeColor={colors['border']};strokeWidth=2;"
                "verticalAlign=top;align=center;"
                f"fontSize=16;fontStyle=1;fontColor={colors['text']};"
                f"spacingTop=8;spacingLeft=4;spacingRight=4;"
            ),
            x=x,
            y=y,
            width=width,
            height=height,
            render={
                "kind": "rect",
                "fill": colors["fill"],
                "border": colors["border"],
                "text_color": colors["text"],
                "font_size": 16,
                "bold": True,
                "align": "center",
                "valign": "top",
                "radius": 10,
                "border_width": 2,
            },
        )

        count = max(len(modules), 1)
        pad = preset["module_padding_x"]
        gap = preset["module_gap"]
        free_width = width - pad * 2 - gap * (count - 1)
        module_width = max(free_width / count, 40)
        module_y = y + preset["layer_header_height"]
        module_height = height - preset["layer_header_height"] - preset["module_bottom_padding"]

        for index, module in enumerate(modules):
            mx = x + pad + index * (module_width + gap)
            self.add_vertex(
                value=module,
                style=(
                    "rounded=1;arcSize=14;whiteSpace=wrap;html=1;"
                    "fillColor=#FFFFFF;"
                    f"strokeColor={colors['border']};strokeWidth=1;"
                    "fontSize=12;fontColor=#263238;align=center;verticalAlign=middle;"
                    "shadow=0;"
                ),
                x=mx,
                y=module_y,
                width=module_width,
                height=module_height,
                render={
                    "kind": "rect",
                    "fill": "#FFFFFF",
                    "border": colors["border"],
                    "text_color": "#263238",
                    "font_size": 12,
                    "bold": False,
                    "align": "center",
                    "valign": "middle",
                    "radius": 14,
                    "border_width": 1,
                    "wrap": True,
                },
            )

        return layer_id

    def add_sidebar(
        self,
        name: str,
        x: float,
        y: float,
        width: float,
        height: float,
        colors: Dict[str, str],
        preset: Dict[str, Any],
    ) -> str:
        """Add a full-height vertical bar with vertically stacked text."""
        return self.add_vertex(
            value=_vertical_label(name),
            style=(
                "rounded=0;whiteSpace=wrap;html=1;"
                f"fillColor={colors['fill']};strokeColor={colors['border']};strokeWidth=2;"
                "align=center;verticalAlign=middle;"
                f"fontSize={preset['sidebar_font_size']};fontStyle=1;"
                f"fontColor={colors['text']};spacing=0;"
            ),
            x=x,
            y=y,
            width=width,
            height=height,
            render={
                "kind": "rect",
                "fill": colors["fill"],
                "border": colors["border"],
                "text_color": colors["text"],
                "font_size": preset["sidebar_font_size"],
                "bold": True,
                "align": "center",
                "valign": "middle",
                "radius": 0,
                "border_width": 2,
                "vertical_text": True,
            },
        )

    def add_bidirectional_arrows(
        self,
        upper_id: str,
        lower_id: str,
        preset: Dict[str, Any],
    ) -> List[str]:
        """Connect two adjacent layers with multiple bidirectional arrows."""
        upper = next(v for v in self._vertices if v["id"] == upper_id)
        lower = next(v for v in self._vertices if v["id"] == lower_id)
        ids: List[str] = []
        for fraction in preset["arrow_fractions"]:
            style = (
                "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
                f"startArrow=block;endArrow=block;startFill=1;endFill=1;"
                f"strokeColor={preset['arrow_color']};strokeWidth={preset['arrow_width']};"
                f"exitX={fraction};exitY=1;entryX={fraction};entryY=0;"
            )
            x = upper["x"] + fraction * upper["width"]
            render = {
                "kind": "arrow",
                "x1": x,
                "y1": upper["y"] + upper["height"],
                "x2": lower["x"] + fraction * lower["width"],
                "y2": lower["y"],
                "color": preset["arrow_color"],
                "width": preset["arrow_width"],
            }
            ids.append(self.add_edge(upper_id, lower_id, style, render=render))
        return ids

    # -- serialization ----------------------------------------------------- #

    def to_element(self) -> ET.Element:
        """Build the ``mxfile`` XML element tree."""
        mxfile = ET.Element(
            "mxfile",
            {
                "host": "app.diagrams.net",
                "modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "agent": "drawio-skill-demo",
                "version": "24.0.0",
                "type": "device",
            },
        )
        diagram = ET.SubElement(
            mxfile,
            "diagram",
            {"id": _short_id(), "name": self.title},
        )
        model = ET.SubElement(
            diagram,
            "mxGraphModel",
            {
                "dx": "1600",
                "dy": "1000",
                "grid": "0",
                "gridSize": "10",
                "guides": "1",
                "tooltips": "1",
                "connect": "1",
                "arrows": "1",
                "fold": "1",
                "page": "1",
                "pageScale": "1",
                "pageWidth": str(self.page_width),
                "pageHeight": str(self.page_height),
                "math": "0",
                "shadow": "0",
            },
        )
        root = ET.SubElement(model, "root")
        ET.SubElement(root, "mxCell", {"id": "0"})
        ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

        for vertex in self._vertices:
            cell = ET.SubElement(
                root,
                "mxCell",
                {
                    "id": vertex["id"],
                    "value": vertex["value"],
                    "style": vertex["style"],
                    "vertex": "1",
                    "parent": "1",
                },
            )
            ET.SubElement(
                cell,
                "mxGeometry",
                {
                    "x": str(vertex["x"]),
                    "y": str(vertex["y"]),
                    "width": str(vertex["width"]),
                    "height": str(vertex["height"]),
                    "as": "geometry",
                },
            )

        for edge in self._edges:
            cell = ET.SubElement(
                root,
                "mxCell",
                {
                    "id": edge["id"],
                    "value": edge["value"],
                    "style": edge["style"],
                    "edge": "1",
                    "parent": "1",
                    "source": edge["source"],
                    "target": edge["target"],
                },
            )
            ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

        return mxfile

    def to_xml(self) -> str:
        """Return the complete ``.drawio`` XML document as a string."""
        element = self.to_element()
        if hasattr(ET, "indent"):  # Python 3.9+
            ET.indent(element, space="  ")
        body = ET.tostring(element, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"

    def save(self, path: str | Path) -> Path:
        """Write the diagram to *path* and return the resolved path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_xml(), encoding="utf-8")
        return target.resolve()

    # -- PNG rendering ----------------------------------------------------- #

    def render_png(self, path: str | Path, scale: int = 2, background: str = "#FFFFFF") -> Path:
        """Render the same geometry as the draw.io file into a PNG.

        Pillow is imported lazily so callers that only need ``.drawio`` do not
        have to install it.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("PNG 渲染需要 Pillow: pip install Pillow") from exc

        width = int(self.page_width * scale)
        height = int(self.page_height * scale)
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        font_cache: Dict[Tuple[int, bool], Any] = {}

        def font(size: int, bold: bool) -> Any:
            key = (size, bold)
            if key not in font_cache:
                font_cache[key] = _load_font(size * scale, bold)
            return font_cache[key]

        def scaled_box(vertex: Dict[str, Any]) -> Tuple[int, int, int, int]:
            return (
                int(vertex["x"] * scale),
                int(vertex["y"] * scale),
                int((vertex["x"] + vertex["width"]) * scale),
                int((vertex["y"] + vertex["height"]) * scale),
            )

        def text_lines(value: str, fnt: Any, max_width: int) -> List[str]:
            value = value.replace("<br>", "\n")
            lines: List[str] = []
            for paragraph in value.split("\n"):
                if not paragraph:
                    lines.append("")
                    continue
                current = ""
                for ch in paragraph:
                    candidate = current + ch
                    if current and draw.textlength(candidate, font=fnt) > max_width:
                        lines.append(current)
                        current = ch
                    else:
                        current = candidate
                lines.append(current)
            return lines

        # Draw edges first so vertices overlay their attachment points.
        for edge in self._edges:
            spec = edge.get("render") or {}
            if spec.get("kind") != "arrow":
                continue
            x1 = int(spec["x1"] * scale)
            y1 = int(spec["y1"] * scale)
            x2 = int(spec["x2"] * scale)
            y2 = int(spec["y2"] * scale)
            color = spec["color"]
            line_width = max(int(spec.get("width", 2) * scale), 1)
            draw.line((x1, y1, x1, y2), fill=color, width=line_width)
            draw.line((x2, y1, x2, y2), fill=color, width=line_width)
            head = 10 * scale
            draw.polygon([(x1, y1), (x1 - head / 2, y1 + head), (x1 + head / 2, y1 + head)], fill=color)
            draw.polygon([(x2, y2), (x2 - head / 2, y2 - head), (x2 + head / 2, y2 - head)], fill=color)

        for vertex in self._vertices:
            spec = vertex.get("render") or {}
            kind = spec.get("kind", "rect")
            box = scaled_box(vertex)
            value = vertex["value"]
            size = int(spec.get("font_size", 12))
            bold = bool(spec.get("bold", False))
            fnt = font(size, bold)
            text_color = spec.get("text_color") or spec.get("color") or "#263238"

            if kind == "text":
                lines = text_lines(value, fnt, vertex["width"] * scale)
                _draw_text_block(draw, lines, box, fnt, text_color, spec, scale, Image)
                continue

            fill = spec.get("fill", "#FFFFFF")
            border = spec.get("border", "#999999")
            radius = int(spec.get("radius", 0) * scale)
            border_width = max(int(spec.get("border_width", 1) * scale), 1)
            draw.rounded_rectangle(
                box,
                radius=radius,
                fill=fill,
                outline=border,
                width=border_width,
            )

            if spec.get("vertical_text"):
                lines = value.replace("<br>", "\n").split("\n")
                _draw_text_block(draw, lines, box, fnt, text_color, spec, scale, Image)
            else:
                padding = 8 * scale if spec.get("wrap") else 2 * scale
                lines = text_lines(value, fnt, vertex["width"] * scale - padding * 2)
                _draw_text_block(draw, lines, box, fnt, text_color, spec, scale, Image)

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return target.resolve()


def _load_font(size: int, bold: bool) -> Any:
    """Load a CJK-capable TrueType font, falling back to the default."""
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    if bold:
        candidates = [p for p in candidates if "bd" in p.lower() or "hei" in p.lower()] + candidates
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_text_block(
    draw: Any,
    lines: List[str],
    box: Tuple[int, int, int, int],
    font: Any,
    color: str,
    spec: Dict[str, Any],
    scale: int,
    image_module: Any,
) -> None:
    """Center a block of text inside *box* according to alignment hints."""
    x1, y1, x2, y2 = box
    line_heights = [font.getbbox(line)[3] - font.getbbox(line)[1] for line in lines]
    line_height = max(line_heights, default=font.size)
    total_height = sum(line_heights) + max(len(lines) - 1, 0) * 4 * scale

    align = spec.get("align", "center")
    valign = spec.get("valign", "middle")

    if valign == "top":
        cursor_y = y1 + int(10 * scale)
    elif valign == "bottom":
        cursor_y = y2 - total_height - int(6 * scale)
    else:
        cursor_y = y1 + max((y2 - y1 - total_height) // 2, 0)

    for line, lh in zip(lines, line_heights):
        text_width = draw.textlength(line, font=font)
        if align == "left":
            cursor_x = x1 + int(6 * scale)
        elif align == "right":
            cursor_x = x2 - text_width - int(6 * scale)
        else:
            cursor_x = x1 + max((x2 - x1 - text_width) / 2, 0)
        draw.text((cursor_x, cursor_y), line, font=font, fill=color)
        cursor_y += lh + 4 * scale


# --------------------------------------------------------------------------- #
# Config-driven entry point
# --------------------------------------------------------------------------- #

def build_from_config(
    config: Dict[str, Any],
    output_path: str | Path,
    png_path: str | Path | None = None,
) -> Path:
    """Build and save a layered architecture diagram from a config dict.

    When ``png_path`` is given, a matching PNG preview is rendered from the
    same geometry and its path is returned instead of the ``.drawio`` path.

    Config schema::

        {
          "title": "架构图标题",
          "preset": "layered_architecture",     # optional
          "preset_overrides": {...},            # optional, deep-merged
          "layers": [                           # ordered top -> bottom visually
            {"name": "用户层", "modules": ["模块A", "模块B"]},
            ...
          ],
          "sidebars": [
            {"name": "安全保障体系", "side": "left"},
            {"name": "标准规范体系", "side": "right"}
          ]
        }
    """
    preset_name = config.get("preset", "layered_architecture")
    if preset_name not in PRESETS:
        raise ValueError(f"未知预设: {preset_name!r}; 可用预设: {list(PRESETS)}")
    preset = _deep_merge(PRESETS[preset_name], config.get("preset_overrides", {}))

    layers: List[Dict[str, Any]] = config.get("layers", [])
    if not layers:
        raise ValueError("配置中缺少 layers")

    title = config.get("title", "架构图")
    builder = DrawioBuilder(
        title=title,
        page_width=preset["page_width"],
        page_height=preset["page_height"],
    )
    builder.add_title(preset)

    # Vertical geometry: first config layer is drawn at the top.
    region_top = preset["title_height"] + 54
    layer_h = preset["layer_height"]
    gap = preset["layer_gap"]
    region_height = len(layers) * layer_h + (len(layers) - 1) * gap
    layer_x = preset["layer_margin_x"]
    layer_w = preset["page_width"] - layer_x * 2

    layer_colors = preset["layer_colors"]
    layer_ids: List[str] = []
    for index, layer in enumerate(layers):
        y = region_top + index * (layer_h + gap)
        colors = layer.get("colors") or layer_colors[index % len(layer_colors)]
        layer_ids.append(
            builder.add_layer(
                name=layer["name"],
                modules=layer.get("modules", []),
                x=layer_x,
                y=y,
                width=layer_w,
                height=layer_h,
                colors=colors,
                preset=preset,
            )
        )

    # Bidirectional arrows between every pair of adjacent layers.
    for upper, lower in zip(layer_ids, layer_ids[1:]):
        builder.add_bidirectional_arrows(upper, lower, preset)

    # Side bars spanning all layers.
    side_colors = preset["sidebar_colors"]
    for sidebar in config.get("sidebars", []):
        side = sidebar.get("side", "left")
        if side == "left":
            x = preset["sidebar_margin_x"]
        elif side == "right":
            x = preset["page_width"] - preset["sidebar_margin_x"] - preset["sidebar_width"]
        else:
            raise ValueError(f"未知侧栏方向: {side!r}; 仅支持 left / right")
        builder.add_sidebar(
            name=sidebar["name"],
            x=x,
            y=region_top,
            width=preset["sidebar_width"],
            height=region_height,
            colors=sidebar.get("colors", side_colors),
            preset=preset,
        )

    builder.save(output_path)
    if png_path is not None:
        return builder.render_png(
            png_path,
            scale=preset["png_scale"],
            background=preset["png_background"],
        )
    return Path(output_path).resolve()


def load_config(path: str | Path) -> Dict[str, Any]:
    """Load a JSON config file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    # Self-check: build a minimal diagram next to this file.
    demo_config = {
        "title": "Drawio Builder 自检",
        "layers": [
            {"name": "上层", "modules": ["模块 A", "模块 B"]},
            {"name": "下层", "modules": ["模块 C", "模块 D"]},
        ],
        "sidebars": [
            {"name": "保障体系", "side": "left"},
            {"name": "规范体系", "side": "right"},
        ],
    }
    drawio = Path(__file__).with_name("_selftest.drawio")
    png = Path(__file__).with_name("_selftest.png")
    build_from_config(demo_config, drawio, png)
    print(f"自检 drawio 已生成: {drawio}")
    print(f"自检 PNG 已生成: {png}")

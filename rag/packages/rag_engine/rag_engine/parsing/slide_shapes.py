"""PowerPoint の原本（OOXML）から、スライドの図形と接続線の構造を形式どおりに読む（#5359）。

Office→PDF の変換と画像の読み取り（Vision）では、どの図形からどの図形へ線がつながるかが失われ、
画像だけを見るモデルが接続を読み違えることがある。原本の slide XML にある確定した事実だけを読む:

- 図形の ID（``cNvPr/@id``）・文字・位置
- 接続線（``p:cxnSp``）の始点と終点の図形（``a:stCxn`` / ``a:endCxn``）、始点から終点までの線の
  折れ点（``a:prstGeom`` のカギ線の形と調整値から決まる）と矢印

文字だけの図形（ラベル）がどの接続線に付くかなど、語や配置の意味は判断しない（読み取りのモデルが、
画像とこの構造を見て判断する）。
"""

from __future__ import annotations

import io
import math
import posixpath
import re
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from xml.etree import ElementTree

from rag_engine.parsing.content_labels import content_labels
from rag_engine.parsing.vision_prompt_rules import SOURCE_SLIDE_SHAPES_KEY

__all__ = ["SOURCE_SLIDE_SHAPES_KEY", "PresentationShapes", "SlideShapes", "read_presentation_shapes", "slide_shapes_text"]
# 1 つの図に書く図形・接続線の数と、1 つの図形の文字の長さの上限（prompt と chunk の大きさを抑える）。
MAX_SLIDE_SHAPES = 100
MAX_SLIDE_CONNECTORS = 100
MAX_SHAPE_TEXT_CHARS = 300
# OOXML の角度の単位（1 度 = 60000）。
_ANGLE_UNIT = 60000
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

type Box = tuple[float, float, float, float]
type Point = tuple[float, float]


@dataclass(frozen=True)
class SlideShape:
    """図形の ID・文字・位置（スライドの EMU。回転したグループの中で位置を決められなければ None）。"""

    shape_id: str
    text: str
    box: Box | None


@dataclass(frozen=True)
class SlideConnector:
    """接続線の始点・終点の図形の ID（結ばれていなければ None）と、線の折れ点・矢印。

    ``path`` は始点から終点までの点（曲線は両端だけ。位置を決められなければ空）。
    """

    connector_id: str
    source: str | None
    target: str | None
    path: tuple[Point, ...]
    arrow_at_start: bool
    arrow_at_end: bool

    @property
    def start(self) -> Point | None:
        return self.path[0] if self.path else None

    @property
    def end(self) -> Point | None:
        return self.path[-1] if self.path else None


@dataclass(frozen=True)
class SlideShapes:
    """1 枚のスライドの大きさ（EMU）と図形・接続線（文書の重なりの順）。"""

    width: float
    height: float
    shapes: tuple[SlideShape, ...]
    connectors: tuple[SlideConnector, ...]


@dataclass(frozen=True)
class PresentationShapes:
    """変換した PDF の頁番号ごとの、接続線を持つスライドの構造。

    ``page_count`` は表示するスライドの数（非表示のスライドは Office→PDF の変換で頁にならない）。
    PDF の頁数と合わなければ、頁とスライドの対応を決められないので使わない。
    """

    page_count: int
    slides: Mapping[int, SlideShapes]


@dataclass(frozen=True)
class _Transform:
    """グループの中の座標をスライドの座標へ写す（x' = dx + sx * x）。"""

    sx: float = 1.0
    sy: float = 1.0
    dx: float = 0.0
    dy: float = 0.0

    def point(self, x: float, y: float) -> Point:
        return self.dx + self.sx * x, self.dy + self.sy * y


def read_presentation_shapes(data: bytes) -> PresentationShapes:
    """pptx の原本から、表示するスライドの順（= 変換した PDF の頁）に構造を読む。

    zip・XML が壊れていれば ``zipfile.BadZipFile`` / ``ElementTree.ParseError`` / ``KeyError`` を送る。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        presentation = _xml(archive, "ppt/presentation.xml")
        size = presentation.find("{*}sldSz")
        width = _number(size, "cx") if size is not None else 0.0
        height = _number(size, "cy") if size is not None else 0.0
        targets = _relationship_targets(archive, "ppt/presentation.xml")
        slides: dict[int, SlideShapes] = {}
        page = 0
        for slide_id in presentation.iterfind("{*}sldIdLst/{*}sldId"):
            target = targets.get(slide_id.get(f"{_REL_NS}id") or "")
            if not target:
                continue
            root = _xml(archive, target)
            if root.get("show") in {"0", "false"}:
                continue
            page += 1
            tree = root.find("{*}cSld/{*}spTree")
            if tree is None:
                continue
            shapes: list[SlideShape] = []
            connectors: list[SlideConnector] = []
            _read_tree(tree, _Transform(), shapes, connectors)
            if connectors and width > 0 and height > 0:
                slides[page] = SlideShapes(width, height, tuple(shapes), tuple(connectors))
        return PresentationShapes(page_count=page, slides=slides)


def slide_shapes_text(slide: SlideShapes, *, page_size: tuple[float, float], region: Box) -> str:
    """図の領域（頁の画像の px）にある図形と接続線を、頁の画像の px の座標で書いた文にする。

    領域と重なる図形と、端が領域の中にあるか領域の図形につながる接続線を書き、その接続線の端の図形も
    書く。接続線が無ければ空文字（図形の文字だけなら OCR と同じなので書かない）。
    """
    scale_x, scale_y = page_size[0] / slide.width, page_size[1] / slide.height

    def box_px(box: Box | None) -> Box | None:
        if box is None:
            return None
        return box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y

    def point_px(point: Point | None) -> Point | None:
        return None if point is None else (point[0] * scale_x, point[1] * scale_y)

    def path_text(connector: SlideConnector) -> str:
        return "-".join(_point_text(point_px(point)) for point in connector.path)

    boxes = {shape.shape_id: box_px(shape.box) for shape in slide.shapes}
    in_region = {shape_id for shape_id, box in boxes.items() if box is not None and _intersects(box, region)}
    connectors = [
        connector
        for connector in slide.connectors
        if {connector.source, connector.target} & in_region
        or any(point is not None and _contains(region, point) for point in (point_px(connector.start), point_px(connector.end)))
    ]
    if not connectors:
        return ""
    endpoints = {item for connector in connectors for item in (connector.source, connector.target)}
    shapes = [shape for shape in slide.shapes if shape.shape_id in endpoints or (shape.shape_id in in_region and shape.text)]
    labels = content_labels()
    lines = [f"{labels.field('source_shapes')}:"]
    for shape in shapes[:MAX_SLIDE_SHAPES]:
        position = _box_text(boxes.get(shape.shape_id))
        head = " ".join(part for part in (f"#{shape.shape_id}", position) if part)
        lines.append(f"- {head}: {shape.text}" if shape.text else f"- {head}")
    if len(shapes) > MAX_SLIDE_SHAPES:
        lines.append("- …")
    lines.append(f"{labels.field('source_connectors')}:")
    for connector in connectors[:MAX_SLIDE_CONNECTORS]:
        arrow = {
            (False, True): "→",
            (True, False): "←",
            (True, True): "↔",
        }.get((connector.arrow_at_start, connector.arrow_at_end), "—")
        ends = f"{_shape_ref(connector.source)} {arrow} {_shape_ref(connector.target)}"
        path = path_text(connector)
        lines.append(f"- #{connector.connector_id}: {ends} {path}" if path else f"- #{connector.connector_id}: {ends}")
    if len(connectors) > MAX_SLIDE_CONNECTORS:
        lines.append("- …")
    return "\n".join(lines)


def _read_tree(
    parent: ElementTree.Element,
    transform: _Transform | None,
    shapes: list[SlideShape],
    connectors: list[SlideConnector],
) -> None:
    for node in _shape_nodes(parent):
        tag = _local(node.tag)
        properties = node.find("./*/{*}cNvPr")
        shape_id = (properties.get("id") or "").strip() if properties is not None else ""
        if tag == "grpSp":
            _read_tree(node, _group_transform(node, transform), shapes, connectors)
            continue
        if not shape_id:
            continue
        xfrm = node.find("{*}spPr/{*}xfrm")
        if xfrm is None:
            xfrm = node.find("{*}xfrm")
        if tag == "cxnSp":
            line = node.find("{*}spPr/{*}ln")
            connectors.append(
                SlideConnector(
                    connector_id=shape_id,
                    source=_connection(node, "stCxn"),
                    target=_connection(node, "endCxn"),
                    path=_connector_path(xfrm, node.find("{*}spPr/{*}prstGeom"), transform),
                    arrow_at_start=_has_arrow(line, "headEnd"),
                    arrow_at_end=_has_arrow(line, "tailEnd"),
                )
            )
            continue
        shapes.append(SlideShape(shape_id, _shape_text(node), _shape_box(xfrm, transform)))


def _shape_nodes(parent: ElementTree.Element) -> Iterator[ElementTree.Element]:
    """図形の木の子（互換の選択肢 ``mc:AlternateContent`` は Fallback か最初の Choice の中）。"""
    for child in parent:
        tag = _local(child.tag)
        if tag == "AlternateContent":
            branch = child.find("{*}Fallback")
            if branch is None:
                branch = child.find("{*}Choice")
            if branch is not None:
                yield from _shape_nodes(branch)
        elif tag in {"sp", "cxnSp", "pic", "graphicFrame", "grpSp"}:
            yield child


def _group_transform(node: ElementTree.Element, outer: _Transform | None) -> _Transform | None:
    """グループの子の座標の写し方。回転・反転したグループの中は位置を決めない（None）。"""
    xfrm = node.find("{*}grpSpPr/{*}xfrm")
    if outer is None or xfrm is None:
        return outer if xfrm is None else None
    if _number(xfrm, "rot") or xfrm.get("flipH") in {"1", "true"} or xfrm.get("flipV") in {"1", "true"}:
        return None
    off, ext = xfrm.find("{*}off"), xfrm.find("{*}ext")
    child_off, child_ext = xfrm.find("{*}chOff"), xfrm.find("{*}chExt")
    if off is None or ext is None or child_off is None or child_ext is None:
        return None
    child_cx, child_cy = _number(child_ext, "cx"), _number(child_ext, "cy")
    sx = _number(ext, "cx") / child_cx if child_cx else 1.0
    sy = _number(ext, "cy") / child_cy if child_cy else 1.0
    dx = _number(off, "x") - _number(child_off, "x") * sx
    dy = _number(off, "y") - _number(child_off, "y") * sy
    return _Transform(outer.sx * sx, outer.sy * sy, outer.dx + outer.sx * dx, outer.dy + outer.sy * dy)


def _frame(xfrm: ElementTree.Element | None) -> tuple[float, float, float, float, float] | None:
    """``a:xfrm`` の左上・幅・高さ・回転（度）。"""
    if xfrm is None:
        return None
    off, ext = xfrm.find("{*}off"), xfrm.find("{*}ext")
    if off is None or ext is None:
        return None
    return (
        _number(off, "x"),
        _number(off, "y"),
        _number(ext, "cx"),
        _number(ext, "cy"),
        _number(xfrm, "rot") / _ANGLE_UNIT,
    )


def _shape_box(xfrm: ElementTree.Element | None, transform: _Transform | None) -> Box | None:
    frame = _frame(xfrm)
    if frame is None or transform is None:
        return None
    x, y, width, height, degrees = frame
    corners = [_rotate((x + cx, y + cy), (x + width / 2, y + height / 2), degrees) for cx in (0, width) for cy in (0, height)]
    points = [transform.point(*corner) for corner in corners]
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _connector_path(
    xfrm: ElementTree.Element | None, geometry: ElementTree.Element | None, transform: _Transform | None
) -> tuple[Point, ...]:
    """接続線の始点から終点までの点。

    線は枠の左上から右下へ引き（カギ線は ``bentConnector2``〜``5`` の決まった折れ方と調整値
    ``adj1``〜``adj3``）、反転してから枠の中心で回転する（OOXML の preset geometry）。
    """
    frame = _frame(xfrm)
    if frame is None or transform is None or xfrm is None:
        return ()
    x, y, width, height, degrees = frame
    flip_h, flip_v = xfrm.get("flipH") in {"1", "true"}, xfrm.get("flipV") in {"1", "true"}
    center = (x + width / 2, y + height / 2)
    points = []
    for local_x, local_y in _connector_local_points(geometry, width, height):
        local_x = width - local_x if flip_h else local_x
        local_y = height - local_y if flip_v else local_y
        point = transform.point(*_rotate((x + local_x, y + local_y), center, degrees))
        if not points or points[-1] != point:
            points.append(point)
    return tuple(points)


def _connector_local_points(geometry: ElementTree.Element | None, width: float, height: float) -> list[Point]:
    """反転・回転の前の枠の中の点（左上が始点、右下が終点）。カギ線でなければ両端だけ。"""
    preset = geometry.get("prst") if geometry is not None else ""
    adjust = {"adj1": 50000.0, "adj2": 50000.0, "adj3": 50000.0}
    for guide in geometry.iterfind("{*}avLst/{*}gd") if geometry is not None else ():
        value = (guide.get("fmla") or "").removeprefix("val ").strip()
        try:
            adjust[guide.get("name") or ""] = float(value)
        except ValueError:
            continue
    x1, y2, x3 = (width * adjust["adj1"] / 100000, height * adjust["adj2"] / 100000, width * adjust["adj3"] / 100000)
    if preset == "bentConnector2":
        return [(0, 0), (width, 0), (width, height)]
    if preset == "bentConnector3":
        return [(0, 0), (x1, 0), (x1, height), (width, height)]
    if preset == "bentConnector4":
        return [(0, 0), (x1, 0), (x1, y2), (width, y2), (width, height)]
    if preset == "bentConnector5":
        return [(0, 0), (x1, 0), (x1, y2), (x3, y2), (x3, height), (width, height)]
    return [(0, 0), (width, height)]


def _rotate(point: Point, center: Point, degrees: float) -> Point:
    """画面の座標（y が下向き）で時計回りに回す。"""
    if not degrees:
        return point
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    dx, dy = point[0] - center[0], point[1] - center[1]
    return center[0] + dx * cos - dy * sin, center[1] + dx * sin + dy * cos


def _connection(node: ElementTree.Element, name: str) -> str | None:
    element = node.find(f"{{*}}nvCxnSpPr/{{*}}cNvCxnSpPr/{{*}}{name}")
    value = (element.get("id") or "").strip() if element is not None else ""
    return value or None


def _has_arrow(line: ElementTree.Element | None, name: str) -> bool:
    end = line.find(f"{{*}}{name}") if line is not None else None
    return end is not None and (end.get("type") or "none") != "none"


def _shape_text(node: ElementTree.Element) -> str:
    paragraphs = []
    for paragraph in node.iterfind("{*}txBody/{*}p"):
        text = "".join(run.text or "" for run in paragraph.iter() if _local(run.tag) == "t")
        if text.strip():
            paragraphs.append(text)
    text = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()
    return text if len(text) <= MAX_SHAPE_TEXT_CHARS else text[:MAX_SHAPE_TEXT_CHARS] + "…"


def _box_text(box: Box | None) -> str:
    if box is None:
        return ""
    x0, y0, x1, y1 = (round(value) for value in box)
    return f"({x0},{y0})-({x1},{y1})"


def _shape_ref(shape_id: str | None) -> str:
    return f"#{shape_id}" if shape_id else "?"


def _point_text(point: Point | None) -> str:
    return f"({round(point[0])},{round(point[1])})" if point is not None else ""


def _intersects(box: Box, region: Box) -> bool:
    return box[0] <= region[2] and region[0] <= box[2] and box[1] <= region[3] and region[1] <= box[3]


def _contains(region: Box, point: Point) -> bool:
    return region[0] <= point[0] <= region[2] and region[1] <= point[1] <= region[3]


def _relationship_targets(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """part の関係の ID → 参照先の part の名前（相対の参照を zip の中の名前にする）。"""
    folder, name = posixpath.split(part)
    rels = _xml(archive, posixpath.join(folder, "_rels", f"{name}.rels"))
    targets = {}
    for relation in rels.iterfind("{*}Relationship"):
        target = relation.get("Target") or ""
        if not target or relation.get("TargetMode") == "External":
            continue
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        targets[relation.get("Id") or ""] = resolved
    return targets


def _xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    with archive.open(name) as handle:
        return ElementTree.fromstring(handle.read())


def _number(element: ElementTree.Element | None, name: str) -> float:
    try:
        return float(element.get(name) or 0) if element is not None else 0.0
    except ValueError:
        return 0.0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

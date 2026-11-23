import datetime
import logging
from typing import Optional
from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
try:
    from .MermaidNodeFixer import MermaidNodeFixer
except ImportError:
    from MermaidNodeFixer import MermaidNodeFixer
import shutil
import os
import re
import pypandoc
import subprocess
import tempfile

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DocumentConverter:
    
    DEFAULT_REFERENCE_DOC = "/home/rsc/work/ollama/doc/default-reference_v2.0.docx"

    def __init__(self, reference_doc: str = None):
        self.reference_doc = reference_doc or self.DEFAULT_REFERENCE_DOC
        
    """文档转换器类"""
    def find_matching_end(self,s: str, start: int) -> int:
        """
        从 start 位置（必须是 '(', '[', '{' 之一）开始，找到与之匹配的结束括号位置。
        返回结束索引，若未找到则返回 -1。
        """
        if start >= len(s):
            return -1
        opening = s[start]
        closing = { '(': ')', '[': ']', '{': '}' }.get(opening)
        if not closing:
            return -1

        stack = []
        for i in range(start, len(s)):
            ch = s[i]
            if ch in '([{':
                stack.append(ch)
            elif ch in ')]}':
                if not stack:
                    continue
                top = stack.pop()
                if (top == '(' and ch == ')') or (top == '[' and ch == ']') or (top == '{' and ch == '}'):
                    if not stack:
                        return i
                else:
                    # 括号不匹配，提前终止
                    return -1
        return -1

    def transform_content(self,text: str) -> str:
        """
        将文本中的所有形如 (xxx) 的括号对（xxx 不含括号）替换为换行符 + xxx。
        支持嵌套括号，会从最内层开始重复替换直到没有括号对。
        """
        pattern = re.compile(r'\(([^()]+)\)')  # 匹配最内层无嵌套的圆括号
        while True:
            new_text = pattern.sub(lambda m: '\n' + m.group(1), text)
            if new_text == text:
                break
            text = new_text
        return text

    def fix_mermaid_node(self,node_str: str) -> str:
        """
        修复单个 Mermaid 节点：
        - 带标识符： A(接口(API))   -> A(接口\nAPI)
        - 匿名节点： (接口(API))     -> (接口\nAPI)
        保留外层形状括号，仅转换形状内部的圆括号。
        """
        if not node_str:
            return node_str

        # 确定节点起始符的位置（形状括号）
        # 如果第一个字符就是形状括号，则为匿名节点
        if node_str[0] in '([{':
            start = 0
        else:
            # 带标识符：找到第一个形状括号
            for i, ch in enumerate(node_str):
                if ch in '([{':
                    start = i
                    break
            else:
                return node_str  # 没有形状括号，原样返回

        end = self.find_matching_end(node_str, start)
        if end == -1:
            return node_str  # 未找到匹配，原样返回

        # 提取形状内部的内容
        inner = node_str[start+1:end]
        # 转换内部圆括号
        transformed_inner = self.transform_content(inner)
        # 重组节点
        return node_str[:start+1] + transformed_inner + node_str[end:]

    def fix_line(self,line: str) -> str:
        """
        修复一行 Mermaid 代码中的所有节点。
        支持同一行中存在多个节点。
        """
        # 跳过可能是 subgraph 定义的行（保护子图边界花括号）
        if line.strip().startswith('subgraph'):
            return line

        # 先处理带标识符的节点，再处理匿名节点
        # 使用循环，每次替换一个节点，直到没有变化
        changed = True
        while changed:
            changed = False
            # 1) 带标识符的节点：标识符 + 形状括号
            id_pattern = re.compile(r'\b([a-zA-Z][a-zA-Z0-9_]*)\s*([\(\[\{])')
            match = id_pattern.search(line)
            if match:
                id_start = match.start(1)
                shape_start = match.start(2)
                end = self.find_matching_end(line, shape_start)
                if end != -1:
                    node_str = line[id_start:end+1]
                    new_node = self.fix_mermaid_node(node_str)
                    if new_node != node_str:
                        line = line[:id_start] + new_node + line[end+1:]
                        changed = True
                        continue  # 重新扫描

            # 2) 匿名节点：形状括号直接开头（前面不是标识符字符）
            anon_pattern = re.compile(r'(?<![a-zA-Z0-9_])([\(\[\{])')
            match = anon_pattern.search(line)
            if match:
                start = match.start(1)
                end = self.find_matching_end(line, start)
                if end != -1:
                    node_str = line[start:end+1]
                    new_node = self.fix_mermaid_node(node_str)
                    if new_node != node_str:
                        line = line[:start] + new_node + line[end+1:]
                        changed = True
                        continue
        return line

    def fix_mermaid_code(self, code: str) -> str:
        """
        修复整个 Mermaid 代码块中的所有节点。
        逐行处理，跳过 subgraph 定义行以保护子图边界。
        """
        lines = code.splitlines()
        fixed_lines = []
        for line in lines:
            # 跳过 subgraph 定义行（保护 { } 不被当作节点处理）
            stripped = line.strip()
            if stripped.startswith('subgraph'):
                fixed_lines.append(line)
            else:
                fixed_lines.append(self.fix_line(line))
        return '\n'.join(fixed_lines)

    def set_cell_shading(self,cell, color):
        """给单元格设置背景色，color为六位十六进制，如 'D9E2F3'"""
        tc_pr = cell._tc.get_or_add_tcPr()
        shading = tc_pr.find(qn('w:shd'))
        if shading is None:
            shading = OxmlElement('w:shd')
            tc_pr.append(shading)
        shading.set(qn('w:fill'), color)
        shading.set(qn('w:val'), 'clear') 
        
    def set_table_width_pct(self, table, pct=100):
        """设置表格宽度为页面宽度的百分比"""
        tbl = table._tbl
        tblPr = tbl.tblPr
        if tblPr is None:
            tblPr = OxmlElement('w:tblPr')
            tbl.insert(0, tblPr)
        tblW = tblPr.find(qn('w:tblW'))
        if tblW is None:
            tblW = OxmlElement('w:tblW')
            tblPr.append(tblW)
        # 百分比类型，值单位是 1/50 %，所以 100% = 5000
        tblW.set(qn('w:type'), 'pct')
        tblW.set(qn('w:w'), str(pct * 50))   
    
    def get_page_usable_size(self, doc):
        """
        安全获取第一页的可用尺寸（宽度、高度），单位 EMU。
        使用 A4 + 1 英寸边距作为缺失默认值。
        """
        section = doc.sections[0]

        # 页宽
        page_width = section.page_width
        if page_width is None:
            page_width = 210 * 36000       # A4 宽 210 mm
        else:
            page_width = int(page_width)

        # 页高
        page_height = section.page_height
        if page_height is None:
            page_height = 297 * 36000      # A4 高 297 mm
        else:
            page_height = int(page_height)

        # 左 / 右 / 上 / 下 边距
        left   = int(section.left_margin) if section.left_margin is not None else 25.4 * 36000
        right  = int(section.right_margin) if section.right_margin is not None else 25.4 * 36000
        top    = int(section.top_margin) if section.top_margin is not None else 25.4 * 36000
        bottom = int(section.bottom_margin) if section.bottom_margin is not None else 25.4 * 36000

        usable_width  = page_width  - left - right
        usable_height = page_height - top  - bottom
        return usable_width, usable_height

    def limit_image_size(self, image_element, max_width_emu, max_height_emu):
        """
        等比缩放图片，使宽度 ≤ max_width_emu 且 高度 ≤ max_height_emu
        """
        extent = image_element.find(qn('wp:extent'))
        if extent is None:
            return
        cx = int(extent.get('cx'))   # 原宽度
        cy = int(extent.get('cy'))   # 原高度

        # 如果原尺寸已经符合要求，不处理
        if cx <= max_width_emu and cy <= max_height_emu:
            return

        # 计算需要的缩放比例（取宽度和高度中限制更严的那个）
        scale_w = max_width_emu / cx if cx > 0 else 1.0
        scale_h = max_height_emu / cy if cy > 0 else 1.0
        scale = min(scale_w, scale_h)

        new_cx = int(cx * scale)
        new_cy = int(cy * scale)
        extent.set('cx', str(new_cx))
        extent.set('cy', str(new_cy))

    def set_table_border(self, doc):
        for table in doc.tables:
            # 设置表格边框
            tbl = table._tbl
            tblPr = tbl.tblPr if tbl.tblPr is not None else tbl.insert_element_before('w:tblPr')
            borders = tblPr.find(qn('w:tblBorders'))
            if borders is None:
                borders = tblPr.makeelement(qn('w:tblBorders'), {})
                tblPr.append(borders)
            # 四周边框线型（single 实线，大小单位是 1/8 pt，4 即 0.5pt）
            for edge in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
                element = borders.makeelement(qn(f'w:{edge}'), {
                    qn('w:val'): 'single',
                    qn('w:sz'): '4',
                    qn('w:space'): '0',
                    qn('w:color'): '000000'
                })
                borders.append(element)
                # ---------- 2. 宽度 100% ----------
            self.set_table_width_pct(table, 100)
            # ---------- 2. 表头加粗 + 底纹 ----------
            if table.rows:   # 确保表格至少有一行
                header_row = table.rows[0]
                for cell in header_row.cells:
                    # 设置底纹（浅蓝色，可以换成你喜欢的颜色）
                    self.set_cell_shading(cell, 'D9D9D9') # D9E2F3
                    
                    # 加粗表头文字（单元格中可能存在多个段落，全部处理）
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.bold = True    
    
    def process_images(self, doc):    
        # ====== 图片处理：居中 + 限制大小 ======
        max_w, max_h = self.get_page_usable_size(doc)   # 获取可用宽高
        # 2. 图片居中 + 宽高同时限制
        for paragraph in doc.paragraphs:
            for run in paragraph.runs:
                inlines = run._element.findall('.//' + qn('wp:inline'))
                for inline in inlines:
                    self.limit_image_size(inline, max_w, max_h)
                anchors = run._element.findall('.//' + qn('wp:anchor'))
                for anchor in anchors:
                    self.limit_image_size(anchor, max_w, max_h)
                if inlines or anchors:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
   
    def PrettifyWord(self, doc):
        
        self.set_table_border(doc)
        self.process_images(doc)
        
    # @staticmethod
    def convert_markdown_with_mermaid(self, input_file: str, output_file: str, reference_doc: str = None) -> bool:
        """
        将包含Mermaid代码块的Markdown文件转换为Word文档
        
        Args:
            input_file: 输入Markdown文件路径
            output_file: 输出Word文档路径
            
        Returns:
            bool: 是否转换成功
        """
        try:
            # 1. 读取原始Markdown文件
            with open(input_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 2. 查找所有Mermaid代码块
            mermaid_blocks = re.findall(r'```mermaid\n(.*?)```', content, re.DOTALL)
            mmdc_path = shutil.which('mmdc')
            # 3. 创建一个临时目录存放生成的图片
            with tempfile.TemporaryDirectory() as temp_dir:
                img_paths = []
                # 4. 使用mermaid-cli (mmdc) 将每个代码块转换为图片
                # mytmp='c:/work/tmp/'
                for i, block in enumerate(mermaid_blocks):
                    img_path = os.path.join(temp_dir, f'mermaid_{i}.png')
                    # img_path = os.path.join(mytmp, f'mermaid_{i}.png')
                    block2 = MermaidNodeFixer.fix(block)
                    # 调用mmdc命令
                    try:
                        result = subprocess.run([
                            mmdc_path, '-i', '-',  # 从标准输入读取
                            '-o', img_path,
                            '-e', 'png',         # 输出格式为PNG
                            '-p', "C:/work/aiwork/doc/puppeteer-config.json"
                        ], input=block2.encode('utf-8'), check=True)
                        img_paths.append(img_path)
                        # 5. 在Markdown内容中替换代码块为图片引用
                        if result.returncode == 0:
                            content = content.replace(f'```mermaid\n{block}```', f'![]({img_path})\n', 1)
                    except Exception as e:
                        logger.error(f"转换Mermaid代码块为图片失败: {e}")                      
                
                               
                # 6. 将修改后的Markdown内容写入临时文件
                with open('temp.md', 'w', encoding='utf-8') as f:
                    f.write(content)
                
                # wordMedel = '/home/rsc/work/doc/default-reference_ex.docx'
                # wordMedel = "/home/rsc/work/ollama/doc/default-reference_v2.0.docx"
                # wordMedel = "/home/rsc/work/doc/my_wordmodel.dotx"
                wordMedel = "C:/work/aiwork/doc/default-reference_v2.0.docx"
                # 7. 使用pypandoc进行最终转换
                # pypandoc.convert_file('temp.md', 'docx', outputfile=output_file)
                pypandoc.convert_file('temp.md', 'docx', outputfile=output_file,
                                      extra_args=['--reference-doc', wordMedel])
                
                # pypandoc.convert_file('temp.md', 'docx', outputfile=output_file,
                #                       reference-doc=wordMedel)
                
                

                # cmd = [
                #     'pandoc',
                #     'temp.md',
                #     '--reference-doc', wordMedel,
                #     '-o', output_file
                # ]
                # subprocess.run(cmd, check=True)
                logger.info(f"转换成功！已生成文件: {output_file}")
                return True
                
        except Exception as e:
            logger.error(f"文档转换失败: {e}")
            return False

def _convert_to_word_and_pretty(   
    md_path: str,
    docx_path: str,
    reference_doc: Optional[str] = None
) -> Optional[str]:
    """将 Markdown 转为 Word 并进行美化（表格边框、图片等）

    Args:
        md_path: 源 Markdown 文件路径
        docx_path: 目标 Word 文件路径
        reference_doc: 可选的参考模板路径

    Returns:
        美化后保存的 Word 文件路径，失败返回 None
    """
    # 1. 创建 DocumentConverter 实例（每次调用可灵活指定模板）
    converter = DocumentConverter(reference_doc=reference_doc)

    # 2. Markdown → Word（包含 mermaid 转图片）
    if not converter.convert_markdown_with_mermaid(md_path, docx_path):
        logger.warning("⚠️ Word文档生成失败")
        return None

    logger.info(f"📄 Word文档已生成: {docx_path}")

    # 3. 打开新生成的 Word，进行美化
    doc = Document(docx_path)
    converter.PrettifyWord(doc)

    # 4. 保存美化后的版本（覆盖原文件）
    doc.save(docx_path)
    logger.info(f"✨ Word文档美化完成: {docx_path}")
    return docx_path


# 获取word文档的章节树，并打印章节树的层级关系
class WordChapterExtractor:
    def __init__(self, docx_path):
        """
        初始化Word文档提取器
        :param docx_path: Word文档路径
        """
        self.doc = Document(docx_path)
        self.chapters = []  # 章节树（扁平化列表，包含层级关系）
        self._parse_document()  # 自动解析文档结构

    def _parse_document(self):
        """解析整个Word文档，构建章节树（自动生成层级编号）"""
        current_chapter = None
        chapter_stack = []   # 维护章节层级栈
        counters = []        # 新增：层级计数器，如 [1] -> "1", [1,2] -> "1.2"
        
        for para in self.doc.paragraphs:
            if not para.text.strip():
                continue
            
            is_heading, level = self._is_heading(para)
            
            if is_heading:
                title_text = para.text.strip()
                
                # ---------- 改动开始 ----------
                # 根据层级更新计数器
                # 确保计数器列表长度足够
                while len(counters) < level:
                    counters.append(0)
                # 如果当前层级与计数器长度不一致，截断（处理层级降级）
                if len(counters) > level:
                    counters = counters[:level]
                # 当前层级计数加一
                counters[-1] += 1
                # 生成编号字符串，如 "1", "2.3", "2.3.1"
                chapter_num = '.'.join(str(c) for c in counters) # 用点号 "." 将这些字符串连接起来，形成最终的层级编号
                # ---------- 改动结束 ----------
                
                # 创建新章节对象（使用自动生成的编号）
                new_chapter = {
                    "number": chapter_num,   # 自动生成的编号
                    "title": title_text,
                    "level": level,
                    "content": [],
                    "children": []
                }
                
                # 维护章节栈和父子关系
                while chapter_stack and chapter_stack[-1]["level"] >= level:
                    chapter_stack.pop()
                
                if chapter_stack:
                    chapter_stack[-1]["children"].append(new_chapter)
                else:
                    self.chapters.append(new_chapter)
                
                chapter_stack.append(new_chapter)
                current_chapter = new_chapter
            
            else:
                if current_chapter:
                    formatted_text = self._parse_paragraph_format(para)
                    current_chapter["content"].append({"type": "paragraph", "text": formatted_text})
        
        # 处理表格
        self._parse_tables()
    
    def _parse_document2(self):
        """解析整个Word文档，构建章节树"""
        current_chapter = None
        chapter_stack = []  # 用于维护章节层级的栈
        
        for para in self.doc.paragraphs:
            # 跳过空段落
            if not para.text.strip():
                continue
            
            # 判断是否为标题（支持默认Heading样式和自定义标题样式）
            is_heading, level = self._is_heading(para)
            
            if is_heading:
                # 提取标题文本和编号（处理自动编号和手动编号）
                title_text = para.text.strip()
                chapter_num = self._extract_chapter_number(title_text)
                
                # 创建新章节对象
                new_chapter = {
                    "number": chapter_num,
                    "title": title_text,
                    "level": level,
                    "content": [],  # 存储该章节的内容（段落、表格）
                    "children": []  # 子章节
                }
                
                # 维护章节栈和父子关系
                while chapter_stack and chapter_stack[-1]["level"] >= level:
                    chapter_stack.pop()
                
                if chapter_stack:
                    chapter_stack[-1]["children"].append(new_chapter)
                else:
                    self.chapters.append(new_chapter)
                
                chapter_stack.append(new_chapter)
                current_chapter = new_chapter
            
            else:
                # 如果是普通段落，添加到当前章节的内容中
                if current_chapter:
                    # 处理段落格式（粗体、斜体）
                    formatted_text = self._parse_paragraph_format(para)
                    current_chapter["content"].append({"type": "paragraph", "text": formatted_text})
        
        # 处理表格（表格属于其前面最近的章节）
        self._parse_tables()

    def _is_heading(self, paragraph):
        """判断段落是否为标题，并返回级别"""
        # 方法1：通过样式判断（最准确）
        if paragraph.style.name.startswith("Heading"):
            try:
                level = int(paragraph.style.name.split()[-1])
                if 1 <= level <= 6:
                    return True, level
            except ValueError:
                pass
        
        # 方法2：通过样式类型判断
        if paragraph.style.type == WD_STYLE_TYPE.PARAGRAPH:
            if hasattr(paragraph.style, "base_style"):
                base_style = paragraph.style.base_style
                if base_style and base_style.name.startswith("Heading"):
                    try:
                        level = int(base_style.name.split()[-1])
                        if 1 <= level <= 6:
                            return True, level
                    except ValueError:
                        pass
        
        # 方法3：通过文本开头的编号判断（处理手动编号的标题）
        # text = paragraph.text.strip()
        # if re.match(r"^(\d+\.)+\d+\s", text):  # 匹配 1. 1.1 1.1.1 等
        #     # 计算编号的层级
        #     num_part = re.match(r"^(\d+\.)+\d+", text).group()
        #     level = num_part.count(".")
        #     return True, level
        
        return False, 0

    def _extract_chapter_number(self, title_text):
        """从标题文本中提取章节编号"""
        match = re.match(r"^((\d+\.)*\d+)\s", title_text)
        if match:
            return match.group(1)
        return ""

    def _parse_paragraph_format(self, paragraph):
        """解析段落的格式（粗体、斜体）"""
        formatted_text = ""
        for run in paragraph.runs:
            text = run.text
            if not text:
                continue
            if run.bold:
                formatted_text += f"**{text}**"
            elif run.italic:
                formatted_text += f"*{text}*"
            else:
                formatted_text += text
        return formatted_text

    def _parse_tables(self):
        """解析文档中的表格，并归属到对应的章节"""
        # 先获取所有段落和表格的顺序
        all_elements = []
        for para in self.doc.paragraphs:
            all_elements.append(("paragraph", para))
        for table in self.doc.tables:
            all_elements.append(("table", table))
        
        # 按文档中的顺序排序（python-docx的tables和paragraphs是分开的，需要重新排序）
        # 注意：这是一个简化的排序方法，对于复杂文档可能不准确
        # 更准确的方法需要解析XML，但对于大多数文档足够用
        current_chapter = None
        for element_type, element in all_elements:
            if element_type == "paragraph":
                is_heading, level = self._is_heading(element)
                if is_heading:
                    # 找到对应的章节
                    title_text = element.text.strip()
                    chapter_num = self._extract_chapter_number(title_text)
                    current_chapter = self._find_chapter_by_number(chapter_num)
            elif element_type == "table" and current_chapter:
                # 将表格转换为Markdown格式
                table_md = self._table_to_markdown(element)
                current_chapter["content"].append({"type": "table", "text": table_md})

    def _table_to_markdown(self, table):
        """将Word表格转换为Markdown表格"""
        table_md = []
        # 表头
        header_row = table.rows[0]
        header_cells = [cell.text.strip() for cell in header_row.cells]
        table_md.append("| " + " | ".join(header_cells) + " |")
        table_md.append("| " + " | ".join(["---"] * len(header_cells)) + " |")
        
        # 表内容
        for row in table.rows[1:]:
            row_cells = [cell.text.strip() for cell in row.cells]
            table_md.append("| " + " | ".join(row_cells) + " |")
        
        return "\n".join(table_md)

    def _find_chapter_by_number(self, chapter_num, chapters=None):
        """根据章节编号查找章节（递归）"""
        if chapters is None:
            chapters = self.chapters
        
        for chapter in chapters:
            if chapter["number"] == chapter_num:
                return chapter
            # 递归查找子章节
            found = self._find_chapter_by_number(chapter_num, chapter["children"])
            if found:
                return found
        
        return None

    def get_chapter_content(self, chapter_num, include_children=True):
        """
        获取指定章节的内容
        :param chapter_num: 章节编号，如 "2", "2.3", "2.3.6"
        :param include_children: 是否包含子章节的内容
        :return: 转换后的Markdown文本
        """
        chapter = self._find_chapter_by_number(chapter_num)
        if not chapter:
            raise ValueError(f"未找到章节 {chapter_num}")
        
        return self._chapter_to_markdown(chapter, include_children)

    def _chapter_to_markdown(self, chapter, include_children=True):
        """将章节及其子章节转换为Markdown格式"""
        md_content = []
        
        # 添加章节标题
        md_content.append(f"{chapter['number']} {chapter['title']}")
        
        # 添加章节内容
        for content in chapter["content"]:
            if content["type"] == "paragraph":
                md_content.append(content["text"])
            elif content["type"] == "table":
                md_content.append(content["text"])
        
        # 添加子章节内容
        if include_children:
            for child in chapter["children"]:
                md_content.append(self._chapter_to_markdown(child, include_children=True))
        
        return "\n".join(md_content)

    def _chapter_to_markdownEx(self, chapter, include_children=True):
        """将章节及其子章节转换为Markdown格式"""
        md_content = []
        
        # 添加章节标题
        md_content.append(f"{'#' * chapter['level']} {chapter['title']}\n")
        
        # 添加章节内容
        for content in chapter["content"]:
            if content["type"] == "paragraph":
                md_content.append(content["text"] + "\n")
            elif content["type"] == "table":
                md_content.append(content["text"] + "\n")
        
        # 添加子章节内容
        if include_children:
            for child in chapter["children"]:
                md_content.append(self._chapter_to_markdown(child, include_children=True))
        
        return "\n".join(md_content)
    
    def save_chapter_to_markdown(self, chapter_num, output_path, include_children=True):
        """
        将指定章节保存为Markdown文件
        :param chapter_num: 章节编号
        :param output_path: 输出文件路径
        :param include_children: 是否包含子章节
        """
        md_content = self.get_chapter_content(chapter_num, include_children)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"✅ 章节 {chapter_num} 已保存到 {output_path}")
        return md_content

    def save_all_to_markdown(self, output_path):
        """将整个文档保存为Markdown文件"""
        md_content = []
        for chapter in self.chapters:
            md_content.append(self._chapter_to_markdown(chapter, include_children=True))
        
        full_md = "\n".join(md_content)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_md)
        print(f"✅ 整个文档已保存到 {output_path}")

    def list_all_chapters(self):
        """列出所有章节及其编号"""
        print("📚 文档章节列表：")
        self._print_chapters(self.chapters)
    
    def _print_chapters(self, chapters, indent=0):
        """递归打印章节列表"""
        for chapter in chapters:
            if chapter["content"] == []:
                print("  " * indent + f"{chapter['number']} {chapter['title']} {chapter['level']}")
            else: 
                md_content = ""
                # 添加章节内容
                for content in chapter["content"]:
                    if content["type"] == "paragraph":
                        md_content+=content["text"] + "\n"  # 添加段落内容
                print("  " * indent + f"{chapter['number']} {chapter['title']} {chapter['level']} {md_content}")
            self._print_chapters(chapter["children"], indent + 1)

    def _print_lev_chapters(self, chapters, indent=0, level=1):
        """递归打印章节列表"""
        for chapter in chapters:
            print("  " * indent + f"{chapter['number']} {chapter['title']} {chapter['level']}")
            self._print_chapters(chapter["children"], indent + 1)
            
    def get_outline(self):
        """获取文档大纲"""
        return self.chapters
    
def Merimaid2Word():
    # input_file = '/home/rsc/work/ollama/hdlyjy/1.md'
    input_file = 'C:/work/aiwork/output/WMS智能仓储管理系统技术方案_技术方案_20260822_202143.md'
    # input_file = 'C:/work/aiwork/report_web/output/WMS智能仓储管理系统技术方案_技术方案_实时更新-1.md'
    # output_file = '/home/rsc/work/ollama/hdlyjy/1.docx'
    output_file = 'C:/work/aiwork/output/WMS智能仓储管理系统技术方案_mid1.docx'
    Docconverter = DocumentConverter() 
    # 转换为Word文档        
    if Docconverter.convert_markdown_with_mermaid(input_file, output_file):
        logger.info(f"📄 Word文档已生成: {output_file}")
        doc = Document(output_file)
        converter = DocumentConverter()
        # converter.set_table_border(doc)
        converter.PrettifyWord(doc)
        doc.save(output_file)   
    else:
        logger.warning("⚠️ Word文档生成失败")   

def runExample():    
    # 1. 初始化提取器
    # extractor = WordChapterExtractor("你的文档.docx")
    filepath = f"~/Documents/doc/files/RSC1.TSRZ.JSFA基于组件重构的态势可视化软件-技术方案.docx"
    filepath = f"~/Documents/doc/files/RSC1.TSRZ.XQGG基于组件重构的态势可视化软件-软件需求规格说明.docx"
    filepath = f"~/Documents/company/hdlyjy/核力智-ok.docx"
    filepath = os.path.expanduser(filepath)  #替换为你的文档路径，如 "~/Documents/技术方案.docx"
    extractor = WordChapterExtractor(filepath)
    
    # 2. 列出所有章节（先运行这个确认章节编号是否正确）
    print("=" * 50)
    extractor.list_all_chapters()
    print("=" * 50)
    
    # 3. 提取单个章节（不包含子章节）
    try:
        content_236 = extractor.get_chapter_content("3.2", include_children=False)
        print("\n📄 章节 3.2 的内容：")
        print(content_236)
        
        # 保存到文件
        extractor.save_chapter_to_markdown("3.2", "章节3.2.md", include_children=False)
    except ValueError as e:
        print(e)
    
    # 4. 提取章节及其所有子章节
    try:
        extractor.save_chapter_to_markdown("3", "章节3及其子章节.md", include_children=True)
    except ValueError as e:
        print(e)
    
    # 5. 提取整个文档
    extractor.save_all_to_markdown("完整文档.md")    

def test_table_border():
    # input_file = '/home/rsc/work/ollama/hdlyjy/1.docx'
    # input_file = '/home/rsc/Documents/company/dkjj/低空经济综合服务中心平台技术方案_技术方案_20260725_090817_t.docx'
    input_file = '/home/rsc/work/ollama/output/低空经济综合服务中心平台技术方案_技术方案_20260729_185914_t.docx'
    # output_file = '/home/rsc/work/ollama/hdlyjy/1_b2.docx'
    output_file = '/home/rsc/work/ollama/output/低空经济综合服务中心平台技术方案_技术方案_20260729_185914_tb2.docx'
    doc = Document(input_file)
    converter = DocumentConverter()
    # converter.set_table_border(doc)
    converter.PrettifyWord(doc)
    doc.save(output_file)    
# ------------------------------
# 使用示例
# ------------------------------

def md2word():
    input_file = 'C:/work/aiwork/output/WMS智能仓储管理系统技术方案_技术方案_20260822_202143.md'
    # input_file = 'C:/work/aiwork/output/WMS智能仓储管理系统技术方案_技术方案_2026.md'
    output_file = 'C:/work/aiwork/output/WMS智能仓储管理系统技术方案_v1.2.docx'
    pypandoc.convert_file(source_file=input_file, to='docx', outputfile=output_file)
if __name__ == "__main__":
    # runExample()
    Merimaid2Word()
    # test_table_border()
    # md2word()

    
    # import pypandoc
    # print(pypandoc.get_pandoc_version())
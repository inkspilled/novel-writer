"""导出模块 — 支持 TXT、EPUB、PDF 格式导出。

功能：
- TXT：纯文本导出，合并所有章节
- EPUB：电子书导出，支持目录和格式
- PDF：PDF 导出，支持排版和样式
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional

from .logger import get_logger
from . import project_io

logger = get_logger(__name__)


class Exporter:
    """导出器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.project_info = self._load_project_info()

    def _load_project_info(self) -> dict:
        """加载项目信息。"""
        info_path = self.project_dir / "project.json"
        if info_path.exists():
            try:
                return json.loads(info_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def export_txt(self, output_path: Optional[Path] = None) -> Path:
        """导出为 TXT 格式。"""
        if output_path is None:
            output_path = self.project_dir / "export" / f"{self.project_info.get('title', '小说')}.txt"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 扫描所有章节
        chapters = project_io.scan_chapters(self.project_dir)
        if not chapters:
            raise ValueError("没有找到任何章节")
        
        # 合并内容
        content_parts = []
        
        # 添加标题
        title = self.project_info.get("title", "小说")
        content_parts.append(f"{'='*50}")
        content_parts.append(f"  {title}")
        content_parts.append(f"{'='*50}")
        content_parts.append("")
        
        # 添加目录
        content_parts.append("目录")
        content_parts.append("-"*30)
        for ch in chapters:
            content_parts.append(f"第{ch['number']}章 {ch['title']}")
        content_parts.append("")
        content_parts.append("="*50)
        content_parts.append("")
        
        # 添加各章节内容
        for ch in chapters:
            content = project_io.read_md(ch["content_path"])
            if content:
                content_parts.append(f"第{ch['number']}章 {ch['title']}")
                content_parts.append("-"*30)
                content_parts.append(content)
                content_parts.append("")
                content_parts.append("="*50)
                content_parts.append("")
        
        # 写入文件
        output_path.write_text("\n".join(content_parts), encoding="utf-8")
        logger.info("TXT 导出完成: %s", output_path)
        return output_path

    def export_epub(self, output_path: Optional[Path] = None) -> Path:
        """导出为 EPUB 格式（纯标准库实现，无第三方依赖）。

        EPUB 本质是 ZIP + XHTML，结构：
          mimetype (stored uncompressed)
          META-INF/container.xml
          OEBPS/content.opf
          OEBPS/toc.ncx
          OEBPS/nav.xhtml
          OEBPS/style/default.css
          OEBPS/chapter_N.xhtml
        """
        import zipfile
        import html as html_mod
        from xml.sax.saxutils import escape

        if output_path is None:
            output_path = self.project_dir / "export" / f"{self.project_info.get('title', '小说')}.epub"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        chapters = project_io.scan_chapters(self.project_dir)
        if not chapters:
            raise ValueError("没有找到任何章节")

        title = self.project_info.get("title", "小说")
        author = self.project_info.get("author", "未知")
        uid = f"novel-{hash(title) & 0xFFFFFFFF:08x}"

        # 章节 HTML
        chapter_files: list[tuple[str, str, str]] = []  # (filename, chapter_title, xhtml)
        css = (
            'body{font-family:"SimSun","宋体",serif;line-height:1.8;margin:1em;}'
            'h1{text-align:center;margin-bottom:1em;}'
            'p{text-indent:2em;margin-bottom:0.5em;}'
        )

        # 目录页
        toc_items = "\n".join(
            f'<li><a href="chapter_{ch["number"]}.xhtml">第{ch["number"]}章 {escape(ch["title"])}</a></li>'
            for ch in chapters
        )
        toc_xhtml = self._wrap_xhtml(
            "目录",
            f'<h1>{escape(title)}</h1><h2>目录</h2><ul>{toc_items}</ul>',
        )
        chapter_files.append(("toc.xhtml", "目录", toc_xhtml))

        for ch in chapters:
            content = project_io.read_md(ch["content_path"])
            if not content:
                continue
            body = self._markdown_to_html(content, ch["title"])
            xhtml = self._wrap_xhtml(f"第{ch['number']}章 {ch['title']}", body)
            fname = f"chapter_{ch['number']}.xhtml"
            chapter_files.append((fname, f"第{ch['number']}章 {ch['title']}", xhtml))

        # ── 打包 ZIP ──
        with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype 必须是第一个文件且不压缩
            zf.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr("META-INF/container.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                '  <rootfiles>\n'
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                '  </rootfiles>\n'
                '</container>'
            )
            zf.writestr("OEBPS/style/default.css", css)

            # content.opf
            manifest_items = ['<item id="css" href="style/default.css" media-type="text/css"/>']
            spine_items = []
            for i, (fname, _, _) in enumerate(chapter_files):
                cid = f"ch{i}"
                manifest_items.append(f'<item id="{cid}" href="{fname}" media-type="application/xhtml+xml"/>')
                spine_items.append(f'<itemref idref="{cid}"/>')
            manifest_items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

            zf.writestr("OEBPS/content.opf",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">\n'
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
                f'    <dc:identifier id="uid">{uid}</dc:identifier>\n'
                f'    <dc:title>{escape(title)}</dc:title>\n'
                f'    <dc:creator>{escape(author)}</dc:creator>\n'
                '    <dc:language>zh</dc:language>\n'
                '  </metadata>\n'
                '  <manifest>\n'
                f'    {"<n/>".join(manifest_items).replace("<n/>", chr(10))}\n'
                '  </manifest>\n'
                '  <spine toc="ncx">\n'
                f'    {"<n/>".join(spine_items).replace("<n/>", chr(10))}\n'
                '  </spine>\n'
                '</package>'
            )

            # toc.ncx
            nav_points = []
            for i, (fname, ch_title, _) in enumerate(chapter_files):
                nav_points.append(
                    f'    <navPoint id="np{i}" playOrder="{i+1}">\n'
                    f'      <navLabel><text>{escape(ch_title)}</text></navLabel>\n'
                    f'      <content src="{fname}"/>\n'
                    f'    </navPoint>'
                )
            zf.writestr("OEBPS/toc.ncx",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                f'  <head><meta name="dtb:uid" content="{uid}"/></head>\n'
                f'  <docTitle><text>{escape(title)}</text></docTitle>\n'
                '  <navMap>\n'
                + "\n".join(nav_points) +
                '\n  </navMap>\n</ncx>'
            )

            # 章节 XHTML
            for fname, _, xhtml in chapter_files:
                zf.writestr(f"OEBPS/{fname}", xhtml)

        logger.info("EPUB 导出完成: %s", output_path)
        return output_path

    @staticmethod
    def _wrap_xhtml(title: str, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh" lang="zh">\n'
            '<head>\n'
            f'  <title>{html.escape(title)}</title>\n'
            '  <link rel="stylesheet" type="text/css" href="style/default.css"/>\n'
            '</head>\n'
            f'<body>\n{body}\n</body>\n</html>'
        )

    def export_pdf(self, output_path: Optional[Path] = None) -> Path:
        """导出为 PDF 格式。"""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
            from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError:
            raise ImportError("需要安装 reportlab: pip install reportlab")
        
        if output_path is None:
            output_path = self.project_dir / "export" / f"{self.project_info.get('title', '小说')}.pdf"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 扫描所有章节
        chapters = project_io.scan_chapters(self.project_dir)
        if not chapters:
            raise ValueError("没有找到任何章节")
        
        # 注册中文字体
        try:
            pdfmetrics.registerFont(TTFont("SimSun", "simsun.ttc"))
            font_name = "SimSun"
        except Exception:
            try:
                pdfmetrics.registerFont(TTFont("SimSun", "C:/Windows/Fonts/simsun.ttc"))
                font_name = "SimSun"
            except Exception:
                font_name = "Helvetica"
                logger.warning("未找到中文字体，使用默认字体")
        
        # 创建 PDF 文档
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )
        
        # 定义样式
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "Title",
            parent=styles["Title"],
            fontName=font_name,
            fontSize=24,
            alignment=TA_CENTER,
            spaceAfter=30
        )
        chapter_title_style = ParagraphStyle(
            "ChapterTitle",
            parent=styles["Heading1"],
            fontName=font_name,
            fontSize=18,
            alignment=TA_CENTER,
            spaceAfter=20
        )
        body_style = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=12,
            alignment=TA_JUSTIFY,
            firstLineIndent=24,
            leading=20
        )
        
        # 构建内容
        story = []
        
        # 添加标题页
        title = self.project_info.get("title", "小说")
        story.append(Paragraph(title, title_style))
        story.append(Spacer(1, 50))
        story.append(PageBreak())
        
        # 添加目录
        story.append(Paragraph("目录", chapter_title_style))
        story.append(Spacer(1, 20))
        for ch in chapters:
            story.append(Paragraph(f"第{ch['number']}章 {ch['title']}", body_style))
        story.append(PageBreak())
        
        # 添加各章节
        for ch in chapters:
            content = project_io.read_md(ch["content_path"])
            if content:
                # 添加章节标题
                story.append(Paragraph(f"第{ch['number']}章 {ch['title']}", chapter_title_style))
                story.append(Spacer(1, 20))
                
                # 将内容分段
                paragraphs = content.split("\n\n")
                for para in paragraphs:
                    para = para.strip()
                    if para:
                        # 处理 Markdown 格式
                        if para.startswith("#"):
                            # 标题
                            para = para.lstrip("#").strip()
                            story.append(Paragraph(para, chapter_title_style))
                        else:
                            # 正文
                            # 转义 HTML 特殊字符
                            para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                            story.append(Paragraph(para, body_style))
                
                story.append(PageBreak())
        
        # 生成 PDF
        doc.build(story)
        logger.info("PDF 导出完成: %s", output_path)
        return output_path

    def _markdown_to_html(self, content: str, title: str) -> str:
        """将 Markdown 转换为 HTML。"""
        html_parts = [f"<h1>第{title}章</h1>"]
        
        paragraphs = content.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # 处理标题
            if para.startswith("#"):
                level = 0
                for char in para:
                    if char == "#":
                        level += 1
                    else:
                        break
                para = para.lstrip("#").strip()
                if level == 1:
                    html_parts.append(f"<h1>{para}</h1>")
                elif level == 2:
                    html_parts.append(f"<h2>{para}</h2>")
                elif level == 3:
                    html_parts.append(f"<h3>{para}</h3>")
                else:
                    html_parts.append(f"<h4>{para}</h4>")
            else:
                # 处理段落
                # 转义 HTML 特殊字符
                para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html_parts.append(f"<p>{para}</p>")
        
        return "\n".join(html_parts)

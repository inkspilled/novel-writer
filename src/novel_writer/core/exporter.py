"""导出模块 — 支持 TXT、EPUB、PDF 格式导出。

功能：
- TXT：纯文本导出，合并所有章节
- EPUB：电子书导出，支持目录和格式
- PDF：PDF 导出，支持排版和样式
"""
from __future__ import annotations

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
        """导出为 EPUB 格式。"""
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError:
            raise ImportError("需要安装 ebooklib: pip install ebooklib")
        
        if output_path is None:
            output_path = self.project_dir / "export" / f"{self.project_info.get('title', '小说')}.epub"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 扫描所有章节
        chapters = project_io.scan_chapters(self.project_dir)
        if not chapters:
            raise ValueError("没有找到任何章节")
        
        # 创建 EPUB 书
        book = epub.EpubBook()
        
        # 设置元数据
        title = self.project_info.get("title", "小说")
        book.set_identifier(f"novel-{id(self)}")
        book.set_title(title)
        book.set_language("zh")
        book.add_author(self.project_info.get("author", "未知"))
        
        # 添加样式
        style = """
        body {
            font-family: "SimSun", "宋体", serif;
            line-height: 1.8;
            margin: 1em;
        }
        h1 {
            text-align: center;
            margin-bottom: 1em;
        }
        h2 {
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }
        p {
            text-indent: 2em;
            margin-bottom: 0.5em;
        }
        """
        css = epub.EpubItem(
            uid="style",
            file_name="style/default.css",
            media_type="text/css",
            content=style.encode("utf-8")
        )
        book.add_item(css)
        
        # 创建目录页
        toc_content = f"<h1>{title}</h1>\n<h2>目录</h2>\n<ul>\n"
        for ch in chapters:
            toc_content += f'<li><a href="chapter_{ch["number"]}.xhtml">第{ch["number"]}章 {ch["title"]}</a></li>\n'
        toc_content += "</ul>"
        
        toc_page = epub.EpubHtml(
            title="目录",
            file_name="toc.xhtml",
            lang="zh"
        )
        toc_page.content = toc_content.encode("utf-8")
        toc_page.add_item(css)
        book.add_item(toc_page)
        
        # 创建章节
        epub_chapters = [toc_page]
        for ch in chapters:
            content = project_io.read_md(ch["content_path"])
            if content:
                # 将 Markdown 转换为 HTML
                html_content = self._markdown_to_html(content, ch["title"])
                
                epub_chapter = epub.EpubHtml(
                    title=f"第{ch['number']}章 {ch['title']}",
                    file_name=f"chapter_{ch['number']}.xhtml",
                    lang="zh"
                )
                epub_chapter.content = html_content.encode("utf-8")
                epub_chapter.add_item(css)
                book.add_item(epub_chapter)
                epub_chapters.append(epub_chapter)
        
        # 设置目录
        book.toc = epub_chapters
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        
        # 设置 spine
        book.spine = epub_chapters
        
        # 写入文件
        epub.write_epub(str(output_path), book, {})
        logger.info("EPUB 导出完成: %s", output_path)
        return output_path

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

"""exporter 模块的单元测试。"""
import pytest
import json
from pathlib import Path
from src.novel_writer.core.exporter import Exporter


@pytest.fixture
def project_dir(tmp_path):
    """创建临时项目目录。"""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    (project_dir / "chapters").mkdir()
    (project_dir / "planning").mkdir()
    (project_dir / "export").mkdir()

    # 创建项目信息
    project_info = {
        "title": "测试小说",
        "genre": "玄幻",
        "style": "轻松幽默",
        "author": "测试作者"
    }
    (project_dir / "project.json").write_text(
        json.dumps(project_info, ensure_ascii=False),
        encoding="utf-8"
    )

    # 创建测试章节
    for i in range(1, 4):
        chapter_content = f"# 第{i}章 测试章节{i}\n\n这是第{i}章的内容。" * 10
        (project_dir / "chapters" / f"{i}_测试章节{i}.txt").write_text(
            chapter_content, encoding="utf-8"
        )

    return project_dir


@pytest.fixture
def exporter(project_dir):
    """创建导出器。"""
    return Exporter(project_dir)


class TestExporter:
    """Exporter 测试类。"""

    def test_init(self, exporter):
        """测试初始化。"""
        assert exporter.project_info["title"] == "测试小说"
        assert exporter.project_info["genre"] == "玄幻"

    def test_export_txt(self, exporter, project_dir):
        """测试 TXT 导出。"""
        output_path = exporter.export_txt()
        assert output_path.exists()
        assert output_path.suffix == ".txt"
        content = output_path.read_text(encoding="utf-8")
        assert "测试小说" in content
        assert "第1章" in content
        assert "第2章" in content
        assert "第3章" in content

    def test_export_txt_custom_path(self, exporter, tmp_path):
        """测试自定义路径的 TXT 导出。"""
        output_path = tmp_path / "custom.txt"
        exporter.export_txt(output_path)
        assert output_path.exists()

    def test_export_epub(self, exporter, project_dir):
        """测试 EPUB 导出。"""
        try:
            output_path = exporter.export_epub()
            assert output_path.exists()
            assert output_path.suffix == ".epub"
        except ImportError:
            pytest.skip("需要安装 ebooklib")

    def test_export_pdf(self, exporter, project_dir):
        """测试 PDF 导出。"""
        try:
            output_path = exporter.export_pdf()
            assert output_path.exists()
            assert output_path.suffix == ".pdf"
        except ImportError:
            pytest.skip("需要安装 reportlab")

    def test_export_no_chapters(self, tmp_path):
        """测试没有章节时的导出。"""
        project_dir = tmp_path / "empty_project"
        project_dir.mkdir()
        (project_dir / "chapters").mkdir()
        (project_dir / "project.json").write_text('{}', encoding="utf-8")
        exporter = Exporter(project_dir)
        with pytest.raises(ValueError, match="没有找到任何章节"):
            exporter.export_txt()

    def test_load_project_info(self, exporter):
        """测试加载项目信息。"""
        info = exporter._load_project_info()
        assert info["title"] == "测试小说"

    def test_load_project_info_missing(self, tmp_path):
        """测试加载缺失的项目信息。"""
        project_dir = tmp_path / "no_info"
        project_dir.mkdir()
        exporter = Exporter(project_dir)
        assert exporter.project_info == {}


class TestExporterMarkdownToHtml:
    """_markdown_to_html 测试类。"""

    def test_heading(self, exporter):
        """测试标题转换。"""
        content = "# 一级标题\n\n## 二级标题\n\n### 三级标题"
        html = exporter._markdown_to_html(content, "测试")
        assert "<h1>一级标题</h1>" in html
        assert "<h2>二级标题</h2>" in html
        assert "<h3>三级标题</h3>" in html

    def test_paragraph(self, exporter):
        """测试段落转换。"""
        content = "这是第一段。\n\n这是第二段。"
        html = exporter._markdown_to_html(content, "测试")
        assert "<p>这是第一段。</p>" in html
        assert "<p>这是第二段。</p>" in html

    def test_html_escape(self, exporter):
        """测试 HTML 转义。"""
        content = "包含 <b>HTML</b> 标签 & 特殊字符"
        html = exporter._markdown_to_html(content, "测试")
        assert "&lt;b&gt;" in html
        assert "&amp;" in html

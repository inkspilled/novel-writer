"""运行期目录解析测试 — exe 装在哪，config/data/logs 就长在哪（学 smt）。

打包布局与开发目录镜像；安装目录不可写时回退系统用户数据目录；
旧版用户数据目录一次性迁移到安装目录 data/。
"""
from __future__ import annotations

import importlib
import json

import pytest

import src.novel_writer.core.runtime_paths as rpm
from src.novel_writer.core.runtime_paths import RuntimeDirs, resolve_runtime_dirs


# ── 三件套解析 ──

class TestResolveRuntimeDirs:
    def test_dev_mirrors_project_root(self, tmp_path):
        dirs = resolve_runtime_dirs(frozen=False, dev_root=tmp_path)
        assert dirs.data == tmp_path / "data"
        assert dirs.logs == tmp_path / "logs"
        assert dirs.user_config == tmp_path / "config"

    def test_frozen_writable_install_next_to_exe(self, tmp_path):
        install = tmp_path / "NovelWriter"
        install.mkdir()
        legacy = tmp_path / "legacy"
        dirs = resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=legacy,
            probe_writable=lambda p: True,
        )
        assert dirs.data == install / "data"
        assert dirs.logs == install / "logs"
        assert dirs.user_config == install / "config"

    def test_frozen_readonly_install_falls_back_to_legacy(self, tmp_path):
        install = tmp_path / "ProgramFiles"
        install.mkdir()
        legacy = tmp_path / "legacy"
        dirs = resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=legacy,
            probe_writable=lambda p: False,
        )
        assert dirs.data == legacy
        assert dirs.logs == legacy / "logs"
        assert dirs.user_config == legacy / "config"

    def test_default_follows_current_process_in_dev(self, tmp_path):
        """frozen=None 时按当前进程判断（开发环境跑测试恒为 dev 布局）。"""
        dirs = resolve_runtime_dirs(dev_root=tmp_path)
        assert dirs.data == tmp_path / "data"

    def test_resolution_creates_no_directories(self, tmp_path):
        install = tmp_path / "NovelWriter"
        install.mkdir()
        resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=tmp_path / "legacy",
            probe_writable=lambda p: True,
        )
        assert list(install.iterdir()) == []  # 建目录是使用方（logger/首次写入）的事


# ── 旧数据一次性迁移 ──

class TestLegacyMigration:
    @staticmethod
    def _make_legacy(legacy):
        (legacy / "projects" / "末世求生").mkdir(parents=True)
        (legacy / "projects" / "末世求生" / "meta.json").write_text("{}", encoding="utf-8")
        (legacy / "config.json").write_text('{"api_key": "k"}', encoding="utf-8")
        (legacy / "logs").mkdir()
        (legacy / "logs" / "info.log").write_text("x", encoding="utf-8")

    def test_migrates_projects_and_config_not_logs(self, tmp_path):
        install = tmp_path / "NovelWriter"
        install.mkdir()
        legacy = tmp_path / "legacy"
        self._make_legacy(legacy)

        dirs = resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=legacy,
            probe_writable=lambda p: True,
        )

        assert (dirs.data / "projects" / "末世求生" / "meta.json").exists()
        assert json.loads((dirs.data / "config.json").read_text(encoding="utf-8"))["api_key"] == "k"
        # logs/ 不搬，随旧目录归档
        assert not (dirs.data / "logs").exists()
        # 原目录改名 .bak-YYYYMMDD，防止删掉 data 后重复搬运
        assert not legacy.exists()
        assert list(tmp_path.glob("legacy.bak-*"))

    def test_no_migration_when_data_exists(self, tmp_path):
        install = tmp_path / "NovelWriter"
        (install / "data" / "projects").mkdir(parents=True)
        legacy = tmp_path / "legacy"
        self._make_legacy(legacy)

        resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=legacy,
            probe_writable=lambda p: True,
        )

        # 已有 data 时不搬，旧目录原样保留
        assert legacy.is_dir()
        assert not list(tmp_path.glob("legacy.bak-*"))

    def test_no_migration_without_legacy(self, tmp_path):
        install = tmp_path / "NovelWriter"
        install.mkdir()
        resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=tmp_path / "legacy",
            probe_writable=lambda p: True,
        )
        assert not list(tmp_path.glob("legacy*"))

    def test_migration_failure_is_silent(self, tmp_path, monkeypatch):
        """copytree 中途失败静默放弃：宁可重新开始也不阻断启动。"""
        install = tmp_path / "NovelWriter"
        install.mkdir()
        legacy = tmp_path / "legacy"
        self._make_legacy(legacy)

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(rpm.shutil, "copytree", boom)
        dirs = resolve_runtime_dirs(
            frozen=True, install_dir=install, legacy_dir=legacy,
            probe_writable=lambda p: True,
        )
        assert dirs.data == install / "data"  # 布局照旧指向安装目录
        assert legacy.is_dir()  # 失败不改名，下次启动再试


# ── 随包配置双层解析（app_config reload 模拟打包环境） ──

class TestConfigLayers:
    @pytest.fixture()
    def packaged(self, tmp_path):
        """模拟 frozen：安装目录 tmp_path，内置层 _internal/config，用户层 config。"""
        user_cfg = tmp_path / "config"
        bundled_cfg = tmp_path / "_internal" / "config"
        bundled_cfg.mkdir(parents=True)
        (bundled_cfg / "agents.json").write_text('{"editor": 1}', encoding="utf-8")

        saved = (rpm.resolve_runtime_dirs, rpm.is_frozen, rpm.bundle_dir)
        rpm.resolve_runtime_dirs = lambda **kw: RuntimeDirs(
            data=tmp_path / "data", logs=tmp_path / "logs", user_config=user_cfg,
        )
        rpm.is_frozen = lambda: True
        rpm.bundle_dir = lambda: tmp_path / "_internal"

        import src.novel_writer.core.app_config as acm
        importlib.reload(acm)
        yield acm, user_cfg, bundled_cfg
        # 先还原 runtime_paths 再 reload，避免脏常量滞留会话
        rpm.resolve_runtime_dirs, rpm.is_frozen, rpm.bundle_dir = saved
        importlib.reload(acm)

    def test_layers_point_to_install_dirs(self, packaged):
        acm, user_cfg, bundled_cfg = packaged
        assert acm.USER_CONFIG_DIR == user_cfg
        assert acm.BUNDLED_CONFIG_DIR == bundled_cfg
        assert acm.DATA_DIR == user_cfg.parent / "data"
        assert acm.CONFIG_PATH == acm.DATA_DIR / "config.json"
        assert acm.PROJECTS_DIR == acm.DATA_DIR / "projects"

    def test_read_falls_back_to_bundled_when_user_layer_missing(self, packaged):
        acm, _, bundled_cfg = packaged
        assert acm.resolve_config_path("agents.json") == bundled_cfg / "agents.json"

    def test_user_layer_wins_once_created(self, packaged):
        acm, user_cfg, _ = packaged
        user_cfg.mkdir()
        (user_cfg / "agents.json").write_text('{"editor": 2}', encoding="utf-8")
        assert acm.resolve_config_path("agents.json") == user_cfg / "agents.json"

    def test_agents_save_goes_to_user_layer_never_bundled(self, packaged):
        """保存智能体配置落用户层，内置 _internal/config 保持出厂内容。"""
        acm, user_cfg, bundled_cfg = packaged
        agents = importlib.import_module("src.novel_writer.core.agents")
        importlib.reload(agents)
        try:
            agents.save_agents({"editor": {"name": "编辑"}})
            assert json.loads((user_cfg / "agents.json").read_text(encoding="utf-8"))["editor"]["name"] == "编辑"
            assert json.loads((bundled_cfg / "agents.json").read_text(encoding="utf-8")) == {"editor": 1}
            assert "editor" in agents.load_agents()  # 保存后读取走用户层
        finally:
            importlib.reload(agents)

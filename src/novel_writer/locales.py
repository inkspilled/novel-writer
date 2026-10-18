"""国际化文本管理 - 支持中文/English。"""

STRINGS = {
    # ── 窗口 ──
    "window_title": {"zh": "Novel Writer - AI 小说写作", "en": "Novel Writer - AI Novel Writing"},
    "status_ready": {"zh": "就绪", "en": "Ready"},

    # ── 菜单栏 ──
    "menu_file": {"zh": "文件(&F)", "en": "File(&F)"},
    "menu_new_project": {"zh": "新建项目(&N)", "en": "New Project(&N)"},
    "menu_open_project": {"zh": "打开项目(&O)", "en": "Open Project(&O)"},
    "menu_save_project": {"zh": "保存项目(&S)", "en": "Save Project(&S)"},
    "menu_exit": {"zh": "退出(&Q)", "en": "Exit(&Q)"},
    "menu_agent_manage": {"zh": "智能体管理", "en": "Agent Manager"},

    # ── 一级菜单 ──
    "menu_model_agent": {"zh": "模型与智能体(&M)", "en": "Model & Agent(&M)"},
    "menu_appearance_open": {"zh": "外观设置", "en": "Appearance Settings"},
    "menu_focus_mode": {"zh": "专注模式", "en": "Focus Mode"},
    "status_focus_on": {"zh": "专注模式：只看正文（Ctrl+Shift+F 退出）", "en": "Focus mode: text only (Ctrl+Shift+F to exit)"},
    "status_focus_off": {"zh": "已退出专注模式", "en": "Focus mode off"},
    "menu_model_open": {"zh": "模型设置", "en": "Model Settings"},
    "menu_about_app": {"zh": "关于 Novel Writer", "en": "About Novel Writer"},
    "menu_help": {"zh": "帮助(&H)", "en": "Help(&H)"},
    "menu_settings": {"zh": "设置(&S)", "en": "Settings(&S)"},
    "menu_project_settings": {"zh": "项目设置", "en": "Project Settings"},
    "about_version": {"zh": "版本 0.1.0", "en": "Version 0.1.0"},
    "about_desc": {"zh": "多 Agent 协作的 AI 小说写作桌面应用", "en": "AI novel writing desktop app with multi-agent collaboration"},
    "about_open_data": {"zh": "打开数据目录", "en": "Open data folder"},

    # ── Agent 面板 ──
    "settings_temperature": {"zh": "温度:", "en": "Temperature:"},

    # ── 侧边栏 ──
    "sidebar_no_project": {"zh": "未打开项目", "en": "No Project Open"},
    "sidebar_new": {"zh": "新建项目", "en": "New"},
    "sidebar_open": {"zh": "打开", "en": "Open"},
    "sidebar_chapters": {"zh": "章节", "en": "Chapters"},
    "sidebar_add_chapter": {"zh": "+ 添加章节", "en": "+ Add Chapter"},
    "sidebar_unnamed": {"zh": "未命名项目", "en": "Untitled Project"},
    "sidebar_unnamed_chapter": {"zh": "未命名章节", "en": "Untitled Chapter"},

    # ── 编辑区 ──
    "editor_select_chapter": {"zh": "选择一个章节开始写作", "en": "Select a chapter to start writing"},
    "editor_words": {"zh": "字", "en": "words"},
    "editor_tab_content": {"zh": "正文", "en": "Content"},
    "editor_ph_content": {"zh": "在这里开始写作", "en": "Start writing here"},
    "editor_save": {"zh": "保存", "en": "Save"},

    # ── Agent 面板 ──
    "agent_select": {"zh": "选择一个智能体", "en": "Select an Agent"},
    "agent_ph_input": {"zh": "输入指令给智能体", "en": "Enter instructions for Agent"},
    "agent_empty_hint": {
        "zh": "选择上方智能体开始协作，或直接输入指令",
        "en": "Pick an agent above, or type an instruction below",
    },
    "agent_empty_title": {"zh": "从这里开始", "en": "Start here"},
    "agent_quick_outline": {"zh": "让策划补全大纲", "en": "Ask the planner for an outline"},
    "agent_quick_continue": {"zh": "让写手续写下一章", "en": "Ask the writer for the next chapter"},
    "agent_quick_review": {"zh": "让审核检查最近 3 章", "en": "Ask the reviewer to check the last 3 chapters"},
    "agent_send": {"zh": "发送", "en": "Send"},
    "agent_generating": {"zh": "生成中", "en": "Generating"},
    "agent_clear_chat": {"zh": "清空聊天", "en": "Clear Chat"},
    "agent_thinking": {"zh": "AI 正在思考...", "en": "AI is thinking..."},
    "agent_send_hint": {"zh": "Ctrl+Enter 发送", "en": "Ctrl+Enter to send"},
    "agent_model_default": {"zh": "默认模型", "en": "Default model"},

    # ── 设置对话框 ──
    "settings_save_all": {"zh": "保存全部", "en": "Save All"},
    "settings_save": {"zh": "保存", "en": "Save"},
    "settings_close": {"zh": "关闭", "en": "Close"},
    "settings_cancel": {"zh": "取消", "en": "Cancel"},

    # 外观
    "settings_tab_appearance": {"zh": "外观", "en": "Appearance"},
    "settings_theme_group": {"zh": "主题风格", "en": "Theme Style"},
    "settings_select_theme": {"zh": "选择主题:", "en": "Select Theme:"},
    "settings_custom_colors": {"zh": "自定义配色", "en": "Custom Colors"},
    "settings_apply_now": {"zh": "立即应用", "en": "Apply Now"},
    "settings_language": {"zh": "语言:", "en": "Language:"},
    "color_bg": {"zh": "背景色", "en": "Background"},
    "color_surface": {"zh": "面板色", "en": "Surface"},
    "color_fg": {"zh": "字体色", "en": "Text"},
    "color_fg2": {"zh": "副字体色", "en": "Secondary Text"},
    "color_accent": {"zh": "强调色", "en": "Accent"},
    "color_pick": {"zh": "选择颜色", "en": "Pick Color"},

    # 模型
    "settings_tab_model": {"zh": "模型", "en": "Model"},
    "settings_provider": {"zh": "供应商:", "en": "Provider:"},
    "settings_api_key": {"zh": "API Key:", "en": "API Key:"},
    "settings_ph_api_key": {"zh": "Ollama 无需填写", "en": "Not needed for Ollama"},
    "settings_base_url": {"zh": "Base URL:", "en": "Base URL:"},
    "settings_ph_base_url": {"zh": "API 地址", "en": "API endpoint"},
    "settings_model_name": {"zh": "模型名称:", "en": "Model Name:"},
    "settings_ph_model": {"zh": "如 deepseek-chat、qwen3.5:9b", "en": "e.g. deepseek-chat, qwen3.5:9b"},
    "settings_test_conn": {"zh": "测试连接", "en": "Test Connection"},

    # 已保存模型
    "model_saved_models": {"zh": "已保存模型", "en": "Saved Models"},
    "model_save_as": {"zh": "保存方案", "en": "Save Config"},
    "model_delete_config": {"zh": "删除配置", "en": "Delete Config"},
    "model_use_global": {"zh": "使用全局默认", "en": "Use Global Default"},
    "model_name_prompt": {"zh": "为此模型配置命名:", "en": "Name this model config:"},
    "model_name_exists": {"zh": "'{}' 已存在，是否覆盖？", "en": "'{}' already exists, overwrite?"},
    "model_default_group": {"zh": "默认模型", "en": "Default Model"},
    "model_default_label": {"zh": "当前默认:", "en": "Current Default:"},
    "model_select_default": {"zh": "— 从已保存模型中选择 —", "en": "— Select from saved models —"},
    "model_set_default": {"zh": "设为默认", "en": "Set as Default"},
    "model_default_set": {"zh": "已设为默认模型", "en": "Set as default model"},

    # 智能体
    "settings_tab_agent": {"zh": "智能体", "en": "Agent"},
    "settings_agent_list": {"zh": "智能体列表", "en": "Agent List"},
    "settings_agent_detail": {"zh": "智能体详情", "en": "Agent Details"},
    "settings_agent_id": {"zh": "标识:", "en": "ID:"},
    "settings_ph_agent_id": {"zh": "英文标识", "en": "English identifier"},
    "settings_agent_title": {"zh": "标题:", "en": "Title:"},
    "settings_ph_agent_title": {"zh": "中文名称", "en": "Display name"},
    "settings_agent_emoji": {"zh": "图标:", "en": "Icon:"},
    "settings_ph_emoji": {"zh": "如 📋", "en": "e.g. 📋"},
    "settings_agent_model": {"zh": "专属模型:", "en": "Dedicated Model:"},
    "settings_agent_skills": {"zh": "技能:", "en": "Skills:"},
    "settings_ph_skills": {"zh": "技能1, 技能2", "en": "skill1, skill2"},
    "settings_agent_prompt": {"zh": "提示词:", "en": "Prompt:"},
    "settings_ph_prompt": {"zh": "系统提示词", "en": "System prompt"},
    "settings_btn_add": {"zh": "添加", "en": "Add"},
    "settings_btn_delete": {"zh": "删除", "en": "Delete"},
    "settings_btn_open": {"zh": "打开", "en": "Open"},
    "settings_btn_reset": {"zh": "重置默认", "en": "Reset Default"},

    # ── 对话框 ──
    "dialog_new_project": {"zh": "新建项目", "en": "New Project"},
    "dialog_add_chapter": {"zh": "添加章节", "en": "Add Chapter"},
    "dialog_chapter_title": {"zh": "章节标题:", "en": "Chapter Title:"},
    "dialog_open_project": {"zh": "打开项目", "en": "Open Project"},
    "dialog_select_project": {"zh": "选择项目:", "en": "Select Project:"},
    "dialog_confirm_delete": {"zh": "确认删除", "en": "Confirm Delete"},
    "dialog_prompt": {"zh": "提示", "en": "Prompt"},
    "dialog_error": {"zh": "错误", "en": "Error"},
    "dialog_success": {"zh": "成功", "en": "Success"},
    "dialog_fail": {"zh": "失败", "en": "Failed"},
    "dialog_confirm": {"zh": "确认", "en": "Confirm"},

    # ── 导出 ──
    "menu_export": {"zh": "导出", "en": "Export"},
    "menu_export_txt": {"zh": "导出为 TXT", "en": "Export as TXT"},
    "menu_export_epub": {"zh": "导出为 EPUB", "en": "Export as EPUB"},
    "menu_export_pdf": {"zh": "导出为 PDF", "en": "Export as PDF"},
    "export_success": {"zh": "导出成功", "en": "Export Successful"},
    "export_open_dir": {"zh": "打开所在目录", "en": "Open folder"},
    "export_failed": {"zh": "导出失败", "en": "Export Failed"},
    "export_no_project": {"zh": "请先打开或创建一个项目", "en": "Please open or create a project first"},
    "export_missing_dep": {"zh": "缺少依赖", "en": "Missing Dependency"},

    # ── 状态消息 ──
    "status_created": {"zh": "已创建项目: {}", "en": "Project created: {}"},
    "status_opened": {"zh": "已打开项目: {}", "en": "Project opened: {}"},
    "status_settings_saved": {"zh": "设置已保存", "en": "Settings saved"},
    "status_agent_done": {"zh": "{} 完成", "en": "{} done"},

    # ── 错误/提示 ──
    "msg_no_project": {"zh": "请先创建或打开一个项目", "en": "Please create or open a project first"},
    "msg_no_projects_saved": {"zh": "暂无已保存的项目", "en": "No saved projects"},
    "msg_agent_not_init": {"zh": "智能体 '{}' 未初始化，请先在设置中配置模型。", "en": "Agent '{}' not initialized. Please configure model in settings."},
    "msg_delete_project": {"zh": "确定删除项目 '{}'？\n此操作不可撤销。", "en": "Delete project '{}'?\nThis cannot be undone."},
    "msg_delete_failed": {"zh": "删除项目 '{}' 失败，文件可能被占用，请关闭相关程序后重试。", "en": "Failed to delete project '{}'. Files may be in use; close other programs and retry."},
    "msg_reset_agents": {"zh": "确定重置所有智能体为默认配置？\n自定义智能体将被覆盖。", "en": "Reset all agents to default?\nCustom agents will be overwritten."},
    "msg_delete_agent": {"zh": "删除 '{}'？", "en": "Delete '{}'?"},
    "msg_agent_exists": {"zh": "'{}' 已存在", "en": "'{}' already exists"},
    "msg_input_model": {"zh": "请输入模型名称", "en": "Please enter model name"},
    "msg_open_fail": {"zh": "打开项目失败: {}", "en": "Failed to open project: {}"},

    # ── 内置智能体 ──

    # ── 工作流面板 ──
    "workflow_title": {"zh": "自动写作工作流", "en": "Auto Writing Workflow"},
    "workflow_start": {"zh": "开始执行", "en": "Start"},
    "workflow_stop": {"zh": "停止", "en": "Stop"},
    "workflow_chapter_progress": {"zh": "章节 {}/{}", "en": "Chapter {}/{}"},
    "workflow_no_project": {"zh": "请先打开一个项目", "en": "Please open a project first"},
    "workflow_no_agents": {"zh": "请先配置智能体和模型", "en": "Please configure agents and models first"},
    "workflow_finished": {"zh": "工作流执行完成！", "en": "Workflow completed!"},
    "workflow_stopped": {"zh": "工作流已停止", "en": "Workflow stopped"},
    "workflow_generated": {"zh": "工作流已加载", "en": "Workflow loaded"},
    "workflow_menu": {"zh": "工作流(&W)", "en": "Workflow(&W)"},
    "workflow_menu_open": {"zh": "打开工作流面板", "en": "Open Workflow Panel"},
    "workflow_menu_default": {"zh": "加载默认工作流", "en": "Load Default Workflow"},

    # ── 右键菜单 ──
    "ctx_rename": {"zh": "重命名", "en": "Rename"},
    "ctx_delete": {"zh": "删除", "en": "Delete"},

    # ── 连接测试 ──
}

# 当前语言
_current_lang = "zh"


def set_language(lang: str):
    global _current_lang
    _current_lang = lang if lang in ("zh", "en") else "zh"


def t(key: str, *args) -> str:
    """获取本地化文本，支持 {} 格式化。"""
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_current_lang, entry.get("zh", key))
    if args:
        try:
            return text.format(*args)
        except (IndexError, KeyError):
            return text
    return text


def get_languages() -> list[tuple[str, str]]:
    """返回可选语言列表: [(code, label), ...]"""
    return [("zh", "中文"), ("en", "English")]

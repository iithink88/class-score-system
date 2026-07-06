---
name: class-score-system
version: 3.0.0
description: >
  通用班级学情提分系统 v3.0 — 面向中小学全学科的课堂作业批改智能体。
  支持OCR识别、AI批改、学情分析、个性化报告生成、IMA知识库联动。
  一键部署到桌面，双击 start.bat 即可运行，零基础老师30分钟完成部署。
  其他老师安装本技能后，AI会自动在桌面上构建完整系统。
triggers:
  - 安装班级学情提分系统
  - 班级学情提分系统
  - 安装提分系统
  - 作业批改系统
  - class-score-system
  - 学情分析系统
---

# 通用班级学情提分系统 v3.0 — WorkBuddy Skill

## 概述

本技能为中小学老师提供一个**完整的课堂作业批改与学情分析桌面工具**。

安装后，AI 会在桌面上自动构建以下完整系统：

```
桌面/通用班级学情提分系统/
├── server.py          # 主程序（Python HTTP服务器 + 嵌入式Web UI）
├── config.json        # 配置文件（API密钥、班级信息）
├── start.bat          # 启动脚本（双击运行）
├── 使用说明.txt        # 详细使用文档
└── 学生名单模板.txt    # 名单格式示例
```

---

## 构建流程（AI自动执行）

当用户说「安装班级学情提分系统」时，按以下步骤执行：

### 第一步：在桌面创建系统文件夹

```
通用班级学情提分系统/
├── server.py
├── config.json   （空密钥模板）
├── start.bat
├── 使用说明.txt
└── 学生名单模板.txt
```

### 第二步：写入 server.py

将技能目录中的 `server_template.py` 完整内容写入 `server.py`。

**关键配置（server.py 中的 DEFAULT_CONFIG）**：

```python
DEFAULT_CONFIG = {
    "ocr_api_key": "",           # 用户需填写 sk-开头的DashScope Key
    "ocr_model": "qwen-vl-plus",
    "ocr_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "ark_api_key": "",           # 火山方舟API Key
    "ark_model_id": "",          # 推理接入点ID (ep-开头)
    "ark_base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "ima_client_id": "",         # IMA Client ID
    "ima_api_key": "",          # IMA API Key
    "ima_kb_name": "",
    "subject": "数学",
    "grade": "一年级",
    "class_name": "1班",
    "school_name": "",
    "port": 8099
}
```

### 第三步：写入 start.bat

将技能目录中的 `start_template.bat` 完整内容写入 `start.bat`。

### 第四步：写入 config.json

将技能目录中的 `config_template.json` 完整内容写入 `config.json`。

### 第五步：写入使用说明.txt 和 学生名单模板.txt

将技能目录中的对应文件复制到目标文件夹。

---

## 核心技术架构

### 技术栈
- Python http.server（内置，无需安装Web框架）
- 嵌入式HTML Web UI（浏览器访问 http://127.0.0.1:8099）
- 三方API：通义千问DashScope OCR + 火山方舟ARK AI批改 + IMA OpenAPI

### Python依赖（用户需预装）
```
pip install requests Pillow cos-python-sdk-v5
```

### 核心模块

| 模块 | 功能 | API |
|------|------|-----|
| OCR识别 | 识别印刷题目+手写答案 | 通义千问 DashScope qwen-vl-plus |
| AI批改 | 逐题判分、错因分析、知识点标注 | 火山方舟 ARK doubao-seed-2.0-pro |
| 报告生成 | 个人报告(学号_姓名.txt) + 班级报告(班级总体学情分析.txt) | 本地文件 |
| IMA联动 | 批量上传图片+报告到IMA知识库 | IMA OpenAPI + COS |

---

## API密钥获取指南

### 1. OCR识别（通义千问 DashScope）
- 地址：https://dashscope.console.aliyun.com/
- 步骤：登录 → 左侧「API-KEY管理」→ 创建API KEY
- 格式：`sk-` 开头
- 需开通 `qwen-vl-plus` 模型权限

### 2. AI批改（火山方舟 ARK）
- 地址：https://console.volcengine.com/ark/
- 步骤：在线推理 → 创建推理接入点 → 选择 `doubao-seed-2.0-pro` 模型
- 获取：推理接入点ID（`ep-`开头）和 API Key

### 3. IMA知识库
- 地址：https://ima.qq.com/agent-interface
- 步骤：IMA客户端 → 左上角头像 → API配置 → 复制 Client ID 和 API Key
- 注意：凭证约30天需续期

---

## Web UI 功能（6个标签页）

1. **学生管理**：导入名单(txt)、添加/删除学生、清空数据
2. **作业上传**：选择学生、拖拽上传图片、预览缩略图
3. **批改分析**：一键开始、进度条、实时日志、生成班级报告
4. **报告查看**：列表查看、在线阅读、打开文件夹
5. **IMA同步**：填写知识库名、一键批量上传
6. **设置**：所有API密钥可视化填写、测试连接、保存配置

---

## 使用流程

1. 双击 `start.bat` 启动系统
2. 浏览器自动打开 http://127.0.0.1:8099
3. 进入「设置」标签页，填写三个API密钥
4. 点击「测试连接」验证各API
5. 进入「学生管理」，导入或添加学生名单
6. 进入「作业上传」，选择学生，上传作业图片
7. 进入「批改分析」，点击「开始批改分析」
8. 查看个人报告和班级总体报告
9. （可选）进入「IMA同步」，将结果上传到IMA知识库

---

## 已知问题与修复历史（v3.0包含以下所有修复）

| 问题 | 原因 | 修复 |
|------|------|------|
| 批改报错 WinError 2 | 桌面目录权限不稳定 | 输出目录改用 `tempfile.gettempdir()` |
| IMA测试卡死 | config.json 写入权限被拒 | `save_config()` 加 try/except 容错 |
| 未找到知识库 | API响应字段名不匹配 | 兼容 `info_list`/`kb_id`/`kb_name` 两套字段 |
| IMA同步失败 | COS SDK 未装到运行Python | 安装 `cos-python-sdk-v5` 到运行环境 |
| IMA同步失败 | `media_type` 用字符串而非数字 | 改为数字映射（9=图片, 13=文本） |

---

## 分享给其他老师

1. 将整个 `通用班级学情提分系统` 文件夹打包为 ZIP
2. 发送给其他老师
3. 对方解压后双击 `start.bat`
4. 在「设置」中替换为自己的API密钥
5. 即可使用

---

## 常见问题排查

| 问题 | 原因 | 解决 |
|------|------|------|
| OCR失败 | API Key错误/过期 | 到 dashscope.console.aliyun.com 检查 |
| AI批改失败 | ARK Key或端点ID错误 | 到火山方舟控制台确认 |
| IMA同步失败 | 凭证过期 | 到 ima.qq.com/agent-interface 续期 |
| 启动报错 | 缺少Python依赖 | `pip install requests Pillow cos-python-sdk-v5` |
| 端口被占用 | 8099被其他程序占用 | 修改 config.json 中的 port |

---

## 注意事项

- 所有API密钥存储在 config.json 中，分享时注意脱敏
- IMA凭证需定期续期（约30天）
- 火山方舟端点需选择 doubao-seed-2.0-pro 模型
- DashScope需开通 qwen-vl-plus 模型权限
- 建议单批次不超过50名学生，避免API限流
- 输出报告保存在系统临时目录（如 `C:\Users\用户名\AppData\Local\Temp\班级学情分析_xxx`）

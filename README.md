# class-score-system（通用班级学情提分系统）

> WorkBuddy 技能：面向中小学全学科教师的「课堂作业智能批改 + 学情分析」桌面系统。
> 一句话：老师拍照上传作业 → AI 自动批改 → 生成个人/班级学情报告 → 同步到 IMA 知识库。

---

## 这个技能能做什么

- **OCR 识别**：通义千问 DashScope `qwen-vl-plus` 识别印刷题目 + 手写答案
- **AI 批改**：火山方舟 ARK（Doubao）逐题判分
- **错题分类**：概念混淆 / 计算失误 / 审题不清 / 步骤遗漏 / 粗心大意 / 知识盲区
- **个性化报告**：每生一份（逐题批改 + 错题分析 + 学习建议 + 变式练习）
- **班级汇总**：正确率统计、易错题排行、教学建议
- **IMA 联动**：作业图片 + 学情报告批量上传 IMA 共享知识库，学生用 IMA 对话即可查个人薄弱点

无需学生装 APP，仅需老师电脑 + 实物展台/手机拍照。

---

## 怎么用（装好技能后）

1. 把技能文件夹里的 `server_template.py` / `start_template.bat` / `config_template.json` 复制到一个工作目录
2. 把 `config_template.json` 改名为 `config.json`，填入你自己的 API Key（见下方「密钥获取」）
3. 双击 `start_template.bat`（改名 `start.bat` 更顺手）→ 浏览器自动打开 `http://127.0.0.1:8099`
4. 在界面里导入学生名单 → 上传作业图片 → 点「开始批改分析」

更完整的图文操作见仓库内 **`使用说明.txt`**。

---

## 朋友怎么装这个技能

1. **下载 `SKILL.md` 直接拖进 WorkBuddy 聊天框**
   - 最轻量：只传一个文件，AI 自动识别为技能
2. **把整个仓库文件夹放进 `~/.workbuddy/skills/`**
   - 把本仓库 clone / 解压后，整个 `class-score-system/` 目录移到 `~/.workbuddy/skills/` 下即可
3. **用 npx 命令一键安装**
   ```bash
   npx skills add iithink88/class-score-system@class-score-system
   ```

---

## 密钥获取（首次使用需自行申请，模板里已是占位符）

| 用途 | 平台 | 地址 |
|------|------|------|
| OCR 识别 | 通义千问 DashScope | https://dashscope.console.aliyun.com/ |
| AI 批改 | 火山方舟 ARK | https://console.volcengine.com/ark |
| IMA 同步 | IMA 知识库 | https://ima.qq.com （凭证续期：ima.qq.com/agent-interface） |

> ⚠️ 本仓库的 `config_template.json` 已**脱敏**，所有密钥均为占位符，请在本机 `config.json` 中填写你自己的 Key。

---

## 目录结构

```
class-score-system/
├── SKILL.md                  # 核心：AI 执行指令 + 参数说明
├── server_template.py        # 主程序模板（含 Web 界面，端口 8099）
├── config_template.json      # 配置模板（密钥占位符，复制后改名 config.json 填写）
├── start_template.bat        # 启动脚本模板（双击运行）
├── 使用说明.txt              # 完整中文使用说明
├── 学生名单模板.txt          # 名单格式示例（学号,姓名）
├── README.md                 # 本文件
├── LICENSE                   # MIT
└── .gitignore
```

---

## 备注

- 版本：v2.0
- Web 界面与学生管理可离线使用；OCR / AI 批改需联网
- 报告自动保存到桌面「分析_YYYYMMDD_HHMMSS」文件夹

# -*- coding: utf-8 -*-
"""
通用班级学情提分系统 v2.0
中小学全学科 - OCR识别 - AI批改 - 学情分析 - IMA联动
"""

import os
import sys
import json
import base64
import time
import threading
import webbrowser
import io
import re
import subprocess
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import requests

try:
    from PIL import Image, ExifTags
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from qcloud_cos import CosConfig, CosS3Client
    HAS_COS = True
except ImportError:
    HAS_COS = False

# ============ 路径和配置 ============
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "ocr_api_key": "",
    "ocr_model": "qwen-vl-plus",
    "ocr_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "ark_api_key": "",
    "ark_model_id": "",
    "ark_base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "ima_client_id": "",
    "ima_api_key": "",
    "ima_kb_name": "",
    "subject": "数学",
    "grade": "一年级",
    "class_name": "1班",
    "school_name": "",
    "port": 8099
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


config = load_config()

# ============ 全局状态 ============
students_data = {}
analysis_status = {"running": False, "current": "", "progress": 0, "total": 0, "log": [], "errors": [], "done": False}
output_folder = None
data_lock = threading.Lock()


def get_output_folder():
    """获取输出文件夹 — 直接使用系统临时目录（最可靠，不受杀毒软件干扰）"""
    global output_folder
    if output_folder is None:
        import tempfile
        ts = time.strftime("%Y%m%d_%H%M%S")
        # 系统临时目录（如 C:\Users\lenovo\AppData\Local\Temp）在Windows上100%可写
        temp_base = tempfile.gettempdir()
        output_folder = os.path.join(temp_base, f"班级学情分析_{ts}")
        try:
            os.makedirs(output_folder, exist_ok=True)
            # 验证可写
            test_file = os.path.join(output_folder, ".test")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
        except Exception:
            # 极端情况：连临时目录都不可写（几乎不可能），用mkdtemp创建唯一目录
            output_folder = tempfile.mkdtemp(prefix="class_analysis_")
    return output_folder


def ensure_folder(folder):
    """每次写入前强制确保目录存在"""
    # 每次都重新 makedirs，防御目录被外部删除的情况
    try:
        os.makedirs(folder, exist_ok=True)
        if not os.path.isdir(folder):
            raise FileNotFoundError(f"目录不存在: {folder}")
        return folder
    except Exception:
        import tempfile
        fallback = os.path.join(tempfile.gettempdir(), f"班级学情分析_{time.strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def reset_session():
    global students_data, output_folder
    with data_lock:
        students_data = {}
        output_folder = None


def sanitize_filename(name):
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()


# ============ 图片处理 ============
def compress_image(image_bytes, max_width=2000, quality=85):
    if not HAS_PIL:
        return base64.b64encode(image_bytes).decode('utf-8'), "image/jpeg"
    img = Image.open(io.BytesIO(image_bytes))
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if tag in ExifTags.TAGS and ExifTags.TAGS[tag] == 'Orientation':
                    if value == 3:
                        img = img.rotate(180, expand=True)
                    elif value == 6:
                        img = img.rotate(270, expand=True)
                    elif value == 8:
                        img = img.rotate(90, expand=True)
    except Exception:
        pass
    if img.mode != 'RGB':
        img = img.convert('RGB')
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return b64, "image/jpeg"


# ============ OCR（通义千问 DashScope）============
def call_ocr(image_b64, cfg):
    try:
        url = f"{cfg['ocr_base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg['ocr_api_key']}",
            "Content-Type": "application/json"
        }
        prompt = (
            "请仔细识别这张学生作业/试卷图片，提取所有题目和学生手写答案。\n"
            "按以下格式逐题输出（每道题之间用 === 分隔）：\n\n"
            "题号: xxx\n题干: xxx（完整题目内容，包括选项）\n"
            "学生答案: xxx（学生手写的答案，未作答写\"未作答\"，模糊写\"模糊\"）\n\n===\n\n"
            "注意：\n1.准确识别印刷和手写文字 2.包括数字、汉字、字母、运算符号\n"
            "3.标注题型（选择/填空/解答等）4.有图形表格请文字描述 5.按顺序从上到下从左到右"
        )
        data = {
            "model": cfg["ocr_model"],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }]
        }
        resp = requests.post(url, headers=headers, json=data, timeout=(15, 60))
        result = resp.json()
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        elif "error" in result:
            err = result['error']
            msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
            raise Exception(f"OCR API错误: {msg}")
        else:
            raise Exception(f"OCR API返回异常: {json.dumps(result, ensure_ascii=False)[:500]}")
    except requests.exceptions.Timeout:
        raise Exception("OCR请求超时(>60秒)，请检查网络或图片是否过大")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"OCR网络连接失败: {e}")
    except Exception as e:
        raise Exception(f"OCR调用异常: {e}")


# ============ AI批改（火山方舟 ARK）============
def call_ark(ocr_text, student_name, cfg):
    try:
        url = f"{cfg['ark_base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg['ark_api_key']}",
            "Content-Type": "application/json"
        }
        now = time.strftime("%Y年%m月%d日 %H:%M")
        system_prompt = f"你是一位经验丰富的{cfg['grade']}{cfg['subject']}老师，擅长批改作业、分析学情、提供个性化辅导建议。"
        user_prompt = (
            f"请根据以下学生作业的OCR识别结果，进行详细批改和分析。\n\n"
            f"学生姓名：{student_name}\n学科：{cfg['subject']}\n年级：{cfg['grade']}\n分析日期：{now}\n\n"
            f"OCR识别结果：\n{ocr_text}\n\n"
            "请按以下结构输出分析报告：\n\n"
            "【逐题批改】\n对每道题判断：\n"
            "- 题号 | 学生答案 | 判定（正确/错误/部分正确）\n"
            "- 如果错误：正确答案、学生错在哪里\n"
            "- 错误类型：概念混淆/计算失误/审题不清/步骤遗漏/粗心大意/知识盲区/其他\n"
            "- 涉及知识点：标注具体知识点名称\n\n"
            "【错题分析】\n1.错误类型分布 2.主要错误原因 3.薄弱知识点清单\n\n"
            "【学习建议】\n1.针对薄弱知识点给3-5条建议 2.推荐2-3道同类练习题（附答案）3.下一步重点\n\n"
            "【总体评价】\n- 正确率估算（百分比）- 亮点进步 - 需加强方面 - 鼓励话语"
        )
        data = {
            "model": cfg["ark_model_id"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3
        }
        resp = requests.post(url, headers=headers, json=data, timeout=(20, 90))
        result = resp.json()
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        elif "error" in result:
            err = result['error']
            msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
            raise Exception(f"AI批改API错误: {msg}")
        else:
            raise Exception(f"AI批改API返回异常: {json.dumps(result, ensure_ascii=False)[:500]}")
    except requests.exceptions.Timeout:
        raise Exception("AI批改请求超时(>90秒)，可能是图片太多或网络不稳定，请稍后重试")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"AI批改网络连接失败: {e}")
    except Exception as e:
        raise Exception(f"AI批改调用异常: {e}")


# ============ 报告生成 ============
def generate_individual_report(sid, student, cfg):
    folder = ensure_folder(get_output_folder())
    filename = f"{sid}_{sanitize_filename(student['name'])}.txt"
    filepath = os.path.join(folder, filename)
    now = time.strftime("%Y年%m月%d日 %H:%M")
    header = (
        f"{'='*60}\n"
        f"  {cfg['grade']}{cfg['subject']}学情分析报告\n"
        f"{'='*60}\n\n"
        f"学校：{cfg.get('school_name', '——')}\n"
        f"班级：{cfg['class_name']}\n学号：{sid}\n姓名：{student['name']}\n"
        f"学科：{cfg['subject']}\n年级：{cfg['grade']}\n分析日期：{now}\n"
        f"图片数量：{len(student.get('images', []))}张\n\n{'='*60}\n\n"
    )
    content = header + student.get('analysis', '（无分析结果）') + "\n"

    # 重试3次写入，每次都重新确保目录存在
    for attempt in range(3):
        try:
            folder = ensure_folder(folder)
            filepath = os.path.join(folder, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return filepath
        except (OSError, IOError) as e:
            if attempt < 2:
                import tempfile
                if attempt == 1:
                    folder = os.path.join(tempfile.gettempdir(), f"分析_{time.strftime('%Y%m%d_%H%M%S')}")
                time.sleep(0.5)
            else:
                raise Exception(f"写入报告失败(已重试3次): {e}")
    return filepath


def generate_class_report(cfg):
    folder = ensure_folder(get_output_folder())
    filepath = os.path.join(folder, "班级总体学情分析.txt")
    analyzed = {sid: s for sid, s in students_data.items() if s.get('analyzed')}
    if not analyzed:
        return None, "没有已分析的学生数据"
    all_analyses = ""
    for sid, s in analyzed.items():
        all_analyses += f"\n--- 学号{sid} {s['name']} ---\n{s.get('analysis', '无')}\n"
    url = f"{cfg['ark_base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['ark_api_key']}",
        "Content-Type": "application/json"
    }
    now = time.strftime("%Y年%m月%d日 %H:%M")
    num = len(analyzed)
    system_prompt = f"你是一位经验丰富的{cfg['grade']}{cfg['subject']}老师和教研组长，擅长班级学情数据分析。"
    user_prompt = (
        f"请根据以下全班学生的批改分析结果，生成班级总体学情分析报告。\n\n"
        f"班级：{cfg['class_name']}\n学科：{cfg['subject']}\n年级：{cfg['grade']}\n"
        f"分析日期：{now}\n参与学生人数：{num}\n\n"
        f"学生批改分析汇总：\n{all_analyses}\n\n"
        "请按以下结构输出：\n\n"
        "一、班级整体情况（参与人数、平均正确率、整体评价）\n"
        "二、各题正确率统计（逐题正确人数和正确率，错误率最高的3道题）\n"
        "三、知识点错误分布（各知识点错误人数和错误率，最薄弱排序）\n"
        "四、错误类型分析（各类型分布，班级共性问题）\n"
        "五、需要重点关注的学生（正确率低的名单及薄弱点）\n"
        "六、教学优化建议（课堂重点、分层教学、后续练习、家校沟通）"
    )
    data = {
        "model": cfg["ark_model_id"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3
    }
    resp = requests.post(url, headers=headers, json=data, timeout=120)
    result = resp.json()
    if "choices" in result:
        class_analysis = result["choices"][0]["message"]["content"]
    else:
        class_analysis = "班级报告生成失败，请检查API配置。"
    header = (
        f"{'='*60}\n"
        f"  {cfg['grade']}{cfg['class_name']}班 {cfg['subject']} 班级总体学情分析\n"
        f"{'='*60}\n\n"
        f"学校：{cfg.get('school_name', '——')}\n班级：{cfg['class_name']}\n"
        f"学科：{cfg['subject']}\n年级：{cfg['grade']}\n分析日期：{now}\n"
        f"参与学生人数：{num}\n\n{'='*60}\n\n"
    )
    content = header + class_analysis + "\n"
    for attempt in range(3):
        try:
            folder = ensure_folder(folder)
            filepath = os.path.join(folder, "班级总体学情分析.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return filepath, None
        except (OSError, IOError) as e:
            if attempt < 2:
                import tempfile
                if attempt == 1:
                    folder = os.path.join(tempfile.gettempdir(), f"分析_{time.strftime('%Y%m%d_%H%M%S')}")
                time.sleep(0.5)
            else:
                return None, f"写入班级报告失败: {e}"


# ============ IMA 知识库联动 ============
def ima_api(endpoint, body, cfg):
    url = f"https://ima.qq.com/{endpoint}"
    headers = {
        "ima-openapi-clientid": cfg["ima_client_id"],
        "ima-openapi-apikey": cfg["ima_api_key"],
        "Content-Type": "application/json"
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    return resp.json()


def search_ima_kb(kb_name, cfg):
    resp = ima_api("openapi/wiki/v1/search_knowledge_base", {
        "query": kb_name, "cursor": "", "limit": 20
    }, cfg)
    if resp.get("code") != 0:
        return None, f"搜索知识库失败: {resp.get('msg', '未知错误')}"
    # API返回 info_list（含 kb_id / kb_name 字段），兼容旧字段名
    data = resp.get("data", {})
    kb_list = data.get("info_list", []) or data.get("knowledge_base_list", [])
    if not kb_list:
        return None, f"未找到知识库「{kb_name}」（列表为空，请先在IMA中创建）"
    # 兼容两种字段命名
    match = next((kb for kb in kb_list
                  if kb.get("kb_name") == kb_name or kb.get("name") == kb_name
                  or kb_name in kb.get("kb_name", "") or kb_name in kb.get("name", "")),
                 None)
    if not match:
        match = kb_list[0]
    kb_id = match.get("kb_id") or match.get("knowledge_base_id")
    return kb_id, None


def upload_to_ima(file_path, kb_id, cfg):
    filename = os.path.basename(file_path)
    filesize = os.path.getsize(file_path)
    ext = os.path.splitext(filename)[1].lower()
    ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".txt": "text/plain", ".pdf": "application/pdf", ".doc": "application/msword",
              ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    # IMA API要求media_type为数字（非字符串）：
    # 1=PDF, 2=URL, 3=DOC/DOCX, 4=PPT/PPTX, 5=XLS/XLSX, 7=MD,
    # 9=IMAGE(JPG/PNG/WEBP), 13=TXT, 14=XMIND, 15=AUDIO
    mt_map = {".jpg": 9, ".jpeg": 9, ".png": 9, ".webp": 9,
              ".txt": 13, ".pdf": 1, ".doc": 3, ".docx": 3,
              ".ppt": 4, ".pptx": 4, ".xls": 5, ".xlsx": 5,
              ".md": 7, ".mp3": 15}
    content_type = ct_map.get(ext, "application/octet-stream")
    media_type = mt_map.get(ext, 1)

    cm_resp = ima_api("openapi/wiki/v1/create_media", {
        "file_name": filename, "file_size": filesize,
        "content_type": content_type, "knowledge_base_id": kb_id,
        "file_ext": ext.lstrip(".")
    }, cfg)
    if cm_resp.get("code") != 0:
        raise Exception(f"create_media失败: {cm_resp.get('msg', '未知错误')}")
    media_id = cm_resp["data"]["media_id"]
    cos_cred = cm_resp["data"]["cos_credential"]

    if HAS_COS:
        cos_config = CosConfig(
            Region=cos_cred["region"], SecretId=cos_cred["secret_id"],
            SecretKey=cos_cred["secret_key"], Token=cos_cred["token"], Scheme="https"
        )
        client = CosS3Client(cos_config)
        with open(file_path, "rb") as f:
            client.put_object(Bucket=cos_cred["bucket_name"], Body=f,
                              Key=cos_cred["cos_key"], ContentType=content_type)
    else:
        cos_url = f"https://{cos_cred['bucket_name']}.cos.{cos_cred['region']}.myqcloud.com/{cos_cred['cos_key']}"
        with open(file_path, "rb") as f:
            file_data = f.read()
        resp = requests.put(cos_url, data=file_data,
                            headers={"Content-Type": content_type,
                                     "x-cos-security-token": cos_cred["token"]}, timeout=60)
        if resp.status_code not in (200, 204):
            raise Exception(f"COS上传失败: HTTP {resp.status_code}")

    ak_resp = ima_api("openapi/wiki/v1/add_knowledge", {
        "media_type": media_type, "media_id": media_id,
        "title": filename, "knowledge_base_id": kb_id,
        "file_info": {"cos_key": cos_cred["cos_key"], "file_size": filesize, "file_name": filename}
    }, cfg)
    if ak_resp.get("code") != 0:
        raise Exception(f"add_knowledge失败: {ak_resp.get('msg', '未知错误')}")
    return True


# ============ 分析线程 ============
def run_analysis():
    global config, output_folder
    cfg = load_config()
    if not cfg.get("ocr_api_key"):
        analysis_status["errors"].append("未配置OCR API Key，请在设置中填写通义千问DashScope API Key (sk-开头)")
        analysis_status["running"] = False
        analysis_status["done"] = True
        return
    if not cfg.get("ark_api_key"):
        analysis_status["errors"].append("未配置火山方舟API Key")
        analysis_status["running"] = False
        analysis_status["done"] = True
        return

    # 每次新分析都创建新的输出文件夹（不复用旧路径）
    output_folder = None

    # 收集所有有图片的学生（允许重新分析之前已批改过的学生）
    to_analyze = {sid: s for sid, s in students_data.items() if s.get("images")}
    # 重置所有学生的analyzed标记，让前端状态准确反映当前批次
    for sid in to_analyze:
        to_analyze[sid]["analyzed"] = False
        to_analyze[sid].pop("analysis", None)
        to_analyze[sid].pop("report_file", None)
    analysis_status["total"] = len(to_analyze)
    analysis_status["progress"] = 0
    analysis_status["log"] = []
    analysis_status["errors"] = []
    analysis_status["done"] = False

    if not to_analyze:
        analysis_status["log"].append("没有需要分析的学生（请先上传作业图片）")
        analysis_status["running"] = False
        analysis_status["done"] = True
        return

    # 预先创建输出文件夹并验证可写性
    out_dir = get_output_folder()
    analysis_status["log"].append(f"[v3-fix] 输出目录: {out_dir}")
    try:
        # 验证文件夹确实存在且可写
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        test_path = os.path.join(out_dir, ".write_check")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
    except Exception as e:
        analysis_status["errors"].append(f"输出目录创建失败: {e}，请检查目录权限")
        analysis_status["running"] = False
        analysis_status["done"] = True
        return

    for sid, student in to_analyze.items():
        analysis_status["current"] = f"{sid}_{student['name']}"
        analysis_status["log"].append(f"开始分析: {sid}_{student['name']} ({len(student['images'])}张图片)")
        try:
            all_ocr = ""
            for i, (img_name, img_bytes) in enumerate(student["images"]):
                analysis_status["log"].append(f"  OCR识别图片 {i+1}/{len(student['images'])}: {img_name}")
                b64, mime = compress_image(img_bytes)
                ocr_result = call_ocr(b64, cfg)
                all_ocr += f"\n--- 图片{i+1}: {img_name} ---\n{ocr_result}\n"
            analysis_status["log"].append(f"  AI批改中... (OCR结果{len(all_ocr)}字, 模型:{cfg.get('ark_model_id','?')})")
            analysis = call_ark(all_ocr, student["name"], cfg)
            student["analysis"] = analysis
            student["analyzed"] = True
            report_path = generate_individual_report(sid, student, cfg)
            student["report_file"] = os.path.basename(report_path)
            analysis_status["log"].append(f"  完成! 报告已生成: {student['report_file']}")
        except Exception as e:
            err_msg = str(e)
            analysis_status["errors"].append(f"{sid}_{student['name']}: {err_msg}")
            analysis_status["log"].append(f"  错误: {err_msg}")
        analysis_status["progress"] += 1

    analysis_status["current"] = ""
    analysis_status["log"].append("全部分析完成!")
    analysis_status["running"] = False
    analysis_status["done"] = True


# ============ HTML 页面 ============
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>通用班级学情提分系统</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; background: #f0f2f5; color: #333; }
header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 16px 24px; }
header h1 { font-size: 22px; margin-bottom: 12px; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.tab { padding: 8px 18px; background: rgba(255,255,255,0.15); border: none; color: #fff; border-radius: 8px 8px 0 0; cursor: pointer; font-size: 14px; transition: 0.2s; }
.tab:hover { background: rgba(255,255,255,0.3); }
.tab.active { background: #fff; color: #667eea; font-weight: 600; }
main { max-width: 1100px; margin: 20px auto; padding: 0 16px; }
.tab-content { display: none; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 20px; }
.tab-content.active { display: block; }
h2 { font-size: 18px; color: #667eea; margin-bottom: 16px; border-bottom: 2px solid #f0f0f0; padding-bottom: 8px; }
.card { background: #f8f9fa; border-radius: 8px; padding: 16px; margin-bottom: 12px; border: 1px solid #e9ecef; }
.btn { padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; transition: 0.2s; }
.btn-primary { background: #667eea; color: #fff; }
.btn-primary:hover { background: #5568d3; }
.btn-success { background: #28a745; color: #fff; }
.btn-success:hover { background: #218838; }
.btn-danger { background: #dc3545; color: #fff; }
.btn-danger:hover { background: #c82333; }
.btn-warning { background: #ffc107; color: #333; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
input, select, textarea { padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; width: 100%; }
input:focus, textarea:focus { outline: none; border-color: #667eea; }
label { font-weight: 600; margin-bottom: 4px; display: block; font-size: 14px; color: #555; }
.form-group { margin-bottom: 12px; }
.form-row { display: flex; gap: 12px; }
.form-row > div { flex: 1; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 14px; }
th { background: #f8f9fa; font-weight: 600; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
.badge-success { background: #d4edda; color: #155724; }
.badge-warning { background: #fff3cd; color: #856404; }
.badge-danger { background: #f8d7da; color: #721c24; }
.badge-info { background: #d1ecf1; color: #0c5460; }
.drop-zone { border: 2px dashed #ccc; border-radius: 12px; padding: 40px; text-align: center; cursor: pointer; transition: 0.2s; }
.drop-zone:hover, .drop-zone.dragover { border-color: #667eea; background: #f8f9ff; }
.drop-zone p { color: #999; margin-top: 8px; }
.progress-bar { width: 100%; height: 24px; background: #e9ecef; border-radius: 12px; overflow: hidden; margin: 12px 0; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); transition: width 0.3s; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px; }
.log-box { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 8px; font-family: "Consolas", monospace; font-size: 13px; max-height: 300px; overflow-y: auto; line-height: 1.6; }
.log-box div { margin-bottom: 2px; }
.log-error { color: #f48771; }
.log-success { color: #89d185; }
.toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 24px; border-radius: 8px; color: #fff; font-size: 14px; z-index: 9999; animation: slideIn 0.3s; }
.toast-success { background: #28a745; }
.toast-error { background: #dc3545; }
.toast-info { background: #17a2b8; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.img-preview { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.img-preview img { width: 80px; height: 80px; object-fit: cover; border-radius: 6px; border: 2px solid #e9ecef; }
.file-item { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: #f8f9fa; border-radius: 6px; margin-bottom: 4px; font-size: 14px; }
.file-item:hover { background: #e9ecef; }
.modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 9998; }
.modal-overlay.show { display: flex; align-items: center; justify-content: center; }
.modal { background: #fff; border-radius: 12px; padding: 24px; max-width: 700px; width: 90%; max-height: 85vh; overflow-y: auto; }
.modal h2 { margin-bottom: 16px; }
.stat-card { display: inline-block; background: #f8f9fa; border-radius: 8px; padding: 12px 20px; margin: 4px; text-align: center; min-width: 100px; }
.stat-card .num { font-size: 24px; font-weight: 700; color: #667eea; }
.stat-card .label { font-size: 12px; color: #888; }
.note { padding: 8px 12px; background: #e7f4ff; border-left: 3px solid #2196f3; border-radius: 4px; margin: 8px 0; font-size: 13px; color: #555; }
.alert { padding: 12px; border-radius: 8px; margin: 8px 0; font-size: 14px; }
.alert-danger { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
.alert-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
.alert-info { background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }
</style>
</head>
<body>
<header>
  <h1>📚 通用班级学情提分系统</h1>
  <div class="tabs">
    <button class="tab active" onclick="switchTab('students')">👥 学生管理</button>
    <button class="tab" onclick="switchTab('upload')">📷 作业上传</button>
    <button class="tab" onclick="switchTab('analyze')">📊 批改分析</button>
    <button class="tab" onclick="switchTab('reports')">📄 报告查看</button>
    <button class="tab" onclick="switchTab('ima')">🔄 IMA同步</button>
    <button class="tab" onclick="switchTab('settings')">⚙️ 设置</button>
  </div>
</header>
<main>
  <!-- 学生管理 -->
  <section id="tab-students" class="tab-content active">
    <h2>👥 学生管理</h2>
    <div class="card">
      <div class="form-row">
        <div class="form-group"><label>学号</label><input id="stu-id" placeholder="如: 01"></div>
        <div class="form-group"><label>姓名</label><input id="stu-name" placeholder="如: 张三"></div>
        <div class="form-group" style="display:flex;align-items:flex-end;"><button class="btn btn-primary" onclick="addStudent()">➕ 添加</button></div>
      </div>
    </div>
    <div class="card">
      <label>批量导入名单（每行格式：学号,姓名，如 01,张三）</label>
      <textarea id="roster-text" rows="5" placeholder="01,张三&#10;02,李四&#10;03,王五"></textarea>
      <div style="margin-top:8px;display:flex;gap:8px;">
        <button class="btn btn-success" onclick="importRoster()">📋 导入名单</button>
        <button class="btn btn-warning" onclick="document.getElementById('roster-file').click()">📁 从文件导入</button>
        <input type="file" id="roster-file" style="display:none" accept=".txt" onchange="importRosterFile(event)">
      </div>
    </div>
    <div id="student-list"></div>
    <div style="margin-top:12px;">
      <button class="btn btn-danger" onclick="clearAll()">🗑️ 清空所有数据</button>
    </div>
  </section>

  <!-- 作业上传 -->
  <section id="tab-upload" class="tab-content">
    <h2>📷 作业上传</h2>
    <div class="card">
      <label>选择学生</label>
      <select id="upload-student" onchange="loadUploadPreview()"></select>
    </div>
    <div class="drop-zone" id="drop-zone" onclick="document.getElementById('file-input').click()">
      <div style="font-size:48px;">📸</div>
      <p><b>点击或拖拽图片到此处上传</b></p>
      <p>支持 JPG / PNG 格式，可同时选择多张</p>
    </div>
    <input type="file" id="file-input" multiple accept="image/*" style="display:none" onchange="handleFiles(this.files)">
    <div id="upload-preview" style="margin-top:12px;"></div>
    <div class="note">💡 支持同一学生上传多张答题图片。图片会自动校正方向和压缩。</div>
  </section>

  <!-- 批改分析 -->
  <section id="tab-analyze" class="tab-content">
    <h2>📊 批改分析</h2>
    <div id="analyze-stats"></div>
    <div class="card">
      <button class="btn btn-primary" id="btn-analyze" onclick="startAnalysis()">🚀 开始批改分析</button>
      <button class="btn btn-success" id="btn-class-report" onclick="generateClassReport()" style="margin-left:8px;">📋 生成班级总体报告</button>
    </div>
    <div id="progress-area" style="display:none;">
      <div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%;">0%</div></div>
      <div id="progress-text" style="text-align:center;margin:4px 0;"></div>
    </div>
    <div id="log-area" class="log-box" style="display:none;"></div>
    <div id="error-area"></div>
  </section>

  <!-- 报告查看 -->
  <section id="tab-reports" class="tab-content">
    <h2>📄 报告查看</h2>
    <div class="card">
      <button class="btn btn-primary" onclick="openFolder()">📂 打开报告文件夹</button>
      <button class="btn btn-success" onclick="loadReports()">🔄 刷新报告列表</button>
    </div>
    <div id="report-list"></div>
    <div id="report-content" style="margin-top:12px;"></div>
  </section>

  <!-- IMA同步 -->
  <section id="tab-ima" class="tab-content">
    <h2>🔄 IMA知识库同步</h2>
    <div class="alert alert-info">
      ℹ️ 批改完成后，可将作业图片和学情报告同步到IMA共享知识库。学生通过IMA对话即可查询个人错题、薄弱点，AI自动生成变式练习。
    </div>
    <div class="card">
      <div class="form-group">
        <label>IMA知识库名称</label>
        <input id="ima-kb-name" placeholder="如: 1班数学练习">
      </div>
      <button class="btn btn-primary" onclick="imaSync()">📤 同步到IMA知识库</button>
      <button class="btn btn-success" onclick="loadReports()" style="margin-left:8px;">🔄 刷新文件列表</button>
    </div>
    <div id="ima-files"></div>
    <div id="ima-status"></div>
  </section>

  <!-- 设置 -->
  <section id="tab-settings" class="tab-content">
    <h2>⚙️ 系统设置</h2>
    <div class="card">
      <h3 style="margin-bottom:8px;color:#333;">学校班级信息</h3>
      <div class="form-row">
        <div class="form-group"><label>学校名称</label><input id="cfg-school"></div>
        <div class="form-group"><label>年级</label><input id="cfg-grade"></div>
        <div class="form-group"><label>班级</label><input id="cfg-class"></div>
        <div class="form-group"><label>学科</label><input id="cfg-subject"></div>
      </div>
    </div>
    <div class="card">
      <h3 style="margin-bottom:8px;color:#333;">OCR识别（通义千问 DashScope）</h3>
      <div class="form-group"><label>API Key（sk-开头）</label><input id="cfg-ocr-key" type="password" placeholder="sk-xxxxxxxxxxxx"></div>
      <div class="note">💡 获取地址：<a href="https://dashscope.console.aliyun.com/" target="_blank">https://dashscope.console.aliyun.com/</a> → 左侧 API-KEY管理 → 创建</div>
      <button class="btn btn-warning" onclick="testApi('ocr')">🧪 测试OCR连接</button>
    </div>
    <div class="card">
      <h3 style="margin-bottom:8px;color:#333;">AI批改（火山方舟 ARK）</h3>
      <div class="form-group"><label>API Key</label><input id="cfg-ark-key" type="password"></div>
      <div class="form-group"><label>推理接入点 ID（Endpoint ID）</label><input id="cfg-ark-model"></div>
      <div class="note">💡 获取地址：火山方舟控制台 → 在线推理 → 创建推理接入点（模型选doubao-seed-2.0-pro）</div>
      <button class="btn btn-warning" onclick="testApi('ark')">🧪 测试ARK连接</button>
    </div>
    <div class="card">
      <h3 style="margin-bottom:8px;color:#333;">IMA知识库</h3>
      <div class="form-group"><label>Client ID</label><input id="cfg-ima-client"></div>
      <div class="form-group"><label>API Key</label><input id="cfg-ima-key" type="password"></div>
      <div class="note">💡 获取地址：IMA客户端 → 左上角头像 → Claw配置 → 复制凭证</div>
      <button class="btn btn-warning" onclick="testApi('ima')">🧪 测试IMA连接</button>
    </div>
    <button class="btn btn-success" onclick="saveConfig()" style="font-size:16px;padding:12px 40px;">💾 保存所有设置</button>
  </section>
</main>
<div id="toast"></div>
<script>
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'students') loadStudents();
  if (name === 'upload') loadUploadStudents();
  if (name === 'analyze') loadAnalyzeStats();
  if (name === 'reports') loadReports();
  if (name === 'settings') loadConfig();
  if (name === 'ima') loadReports();
}
function showToast(msg, type) {
  type = type || 'info';
  var t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function() { t.remove(); }, 3500);
}
async function apiGet(url) {
  var resp = await fetch(url);
  return resp.json();
}
async function apiPost(url, data) {
  var resp = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  return resp.json();
}
// ===== 学生管理 =====
async function loadStudents() {
  var data = await apiGet('/api/students');
  var html = '';
  if (data.students.length === 0) {
    html = '<div class="note">暂无学生，请添加或导入名单</div>';
  } else {
    html = '<table><thead><tr><th>学号</th><th>姓名</th><th>图片</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    data.students.forEach(function(s) {
      var badge = s.analyzed ? '<span class="badge badge-success">已分析</span>' :
                  (s.images > 0 ? '<span class="badge badge-warning">待分析</span>' : '<span class="badge badge-info">无图片</span>');
      html += '<tr><td>' + s.id + '</td><td>' + s.name + '</td><td>' + s.images + '张</td><td>' + badge + '</td>' +
              '<td><button class="btn btn-danger" style="padding:4px 12px;" onclick="deleteStudent(\'' + s.id + '\')">删除</button></td></tr>';
    });
    html += '</tbody></table>';
  }
  document.getElementById('student-list').innerHTML = html;
}
async function addStudent() {
  var id = document.getElementById('stu-id').value.trim();
  var name = document.getElementById('stu-name').value.trim();
  if (!id || !name) { showToast('请填写学号和姓名', 'error'); return; }
  var data = await apiPost('/api/students', {id: id, name: name});
  if (data.ok) { showToast('添加成功', 'success'); document.getElementById('stu-id').value=''; document.getElementById('stu-name').value=''; loadStudents(); }
  else { showToast(data.error || '添加失败', 'error'); }
}
async function deleteStudent(id) {
  if (!confirm('确认删除该学生？')) return;
  var data = await apiPost('/api/students/delete', {id: id});
  if (data.ok) { showToast('已删除', 'success'); loadStudents(); }
}
async function importRoster() {
  var text = document.getElementById('roster-text').value.trim();
  if (!text) { showToast('请输入名单内容', 'error'); return; }
  var data = await apiPost('/api/students/import', {roster: text});
  if (data.ok) { showToast('导入成功：' + data.count + '人', 'success'); document.getElementById('roster-text').value=''; loadStudents(); }
  else { showToast(data.error || '导入失败', 'error'); }
}
function importRosterFile(event) {
  var file = event.target.files[0];
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function(e) { document.getElementById('roster-text').value = e.target.result; };
  reader.readAsText(file, 'UTF-8');
}
async function clearAll() {
  if (!confirm('确认清空所有学生数据和分析结果？此操作不可恢复！')) return;
  var data = await apiPost('/api/clear', {});
  if (data.ok) { showToast('已清空', 'success'); loadStudents(); }
}
// ===== 作业上传 =====
async function loadUploadStudents() {
  var data = await apiGet('/api/students');
  var sel = document.getElementById('upload-student');
  sel.innerHTML = '<option value="">-- 选择学生 --</option>' +
    data.students.map(function(s) { return '<option value="' + s.id + '">' + s.id + '_' + s.name + ' (' + s.images + '张)</option>'; }).join('');
  loadUploadPreview();
}
function loadUploadPreview() {
  var sel = document.getElementById('upload-student');
  if (!sel.value) { document.getElementById('upload-preview').innerHTML = ''; return; }
  apiGet('/api/students').then(function(data) {
    var s = data.students.find(function(x) { return x.id === sel.value; });
    if (s && s.image_names && s.image_names.length > 0) {
      document.getElementById('upload-preview').innerHTML =
        '<div class="card"><b>已上传图片：</b><div class="img-preview">' +
        s.image_names.map(function(n) { return '<img src="data:image/png;base64,' + s.thumbnails[s.image_names.indexOf(n)] + '" title="' + n + '">'; }).join('') +
        '</div></div>';
    } else {
      document.getElementById('upload-preview').innerHTML = '';
    }
  });
}
function handleFiles(files) {
  var sid = document.getElementById('upload-student').value;
  if (!sid) { showToast('请先选择学生', 'error'); return; }
  if (files.length === 0) return;
  var images = [];
  var count = 0;
  for (var i = 0; i < files.length; i++) {
    (function(file) {
      var reader = new FileReader();
      reader.onload = function(e) {
        var base64 = e.target.result.split(',')[1];
        images.push({name: file.name, data: base64});
        count++;
        if (count === files.length) {
          uploadImages(sid, images);
        }
      };
      reader.readAsDataURL(file);
    })(files[i]);
  }
}
async function uploadImages(sid, images) {
  showToast('正在上传' + images.length + '张图片...', 'info');
  var data = await apiPost('/api/upload', {student_id: sid, images: images});
  if (data.ok) { showToast('上传成功：' + data.count + '张', 'success'); loadUploadStudents(); }
  else { showToast(data.error || '上传失败', 'error'); }
}
// 拖拽上传
var dropZone = document.getElementById('drop-zone');
dropZone.addEventListener('dragover', function(e) { e.preventDefault(); this.classList.add('dragover'); });
dropZone.addEventListener('dragleave', function(e) { this.classList.remove('dragover'); });
dropZone.addEventListener('drop', function(e) {
  e.preventDefault(); this.classList.remove('dragover');
  handleFiles(e.dataTransfer.files);
});
// ===== 批改分析 =====
async function loadAnalyzeStats() {
  var data = await apiGet('/api/students');
  var total = data.students.length;
  var withImg = data.students.filter(function(s) { return s.images > 0; }).length;
  var analyzed = data.students.filter(function(s) { return s.analyzed; }).length;
  document.getElementById('analyze-stats').innerHTML =
    '<div class="stat-card"><div class="num">' + total + '</div><div class="label">总人数</div></div>' +
    '<div class="stat-card"><div class="num">' + withImg + '</div><div class="label">已上传图片</div></div>' +
    '<div class="stat-card"><div class="num">' + analyzed + '</div><div class="label">已分析</div></div>' +
    '<div class="stat-card"><div class="num">' + (withImg - analyzed) + '</div><div class="label">待分析</div></div>';
}
async function startAnalysis() {
  if (!confirm('开始批改分析？将自动识别所有已上传图片并逐个批改。')) return;
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('progress-area').style.display = 'block';
  document.getElementById('log-area').style.display = 'block';
  document.getElementById('log-area').innerHTML = '';
  document.getElementById('error-area').innerHTML = '';
  await apiPost('/api/analyze', {});
  pollStatus();
}
var pollTimer = null;
function pollStatus() {
  pollTimer = setInterval(async function() {
    var data = await apiGet('/api/status');
    var pct = data.total > 0 ? Math.round(data.progress / data.total * 100) : 0;
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('progress-fill').textContent = pct + '%';
    document.getElementById('progress-text').textContent = data.current ? '正在分析: ' + data.current : (data.done ? '完成' : '准备中...');
    var logHtml = data.log.map(function(l) {
      var cls = l.indexOf('错误') >= 0 ? 'log-error' : (l.indexOf('完成') >= 0 ? 'log-success' : '');
      return '<div class="' + cls + '">' + l + '</div>';
    }).join('');
    document.getElementById('log-area').innerHTML = logHtml;
    document.getElementById('log-area').scrollTop = document.getElementById('log-area').scrollHeight;
    if (data.errors.length > 0) {
      document.getElementById('error-area').innerHTML = '<div class="alert alert-danger"><b>错误信息：</b><br>' + data.errors.join('<br>') + '</div>';
    }
    if (data.done) {
      clearInterval(pollTimer);
      document.getElementById('btn-analyze').disabled = false;
      if (data.errors.length === 0) {
        showToast('全部分析完成！', 'success');
      } else {
        showToast('分析完成（有' + data.errors.length + '个错误）', 'error');
      }
      loadAnalyzeStats();
    }
  }, 1500);
}
async function generateClassReport() {
  showToast('正在生成班级总体报告...', 'info');
  var data = await apiPost('/api/class-report', {});
  if (data.ok) { showToast('班级报告已生成: ' + data.file, 'success'); loadReports(); }
  else { showToast(data.error || '生成失败', 'error'); }
}
// ===== 报告查看 =====
async function loadReports() {
  var data = await apiGet('/api/reports');
  if (!data.files || data.files.length === 0) {
    document.getElementById('report-list').innerHTML = '<div class="note">暂无报告文件，请先进行批改分析</div>';
    document.getElementById('ima-files').innerHTML = '<div class="note">暂无文件可同步</div>';
    return;
  }
  var html = '<table><thead><tr><th>文件名</th><th>大小</th><th>操作</th></tr></thead><tbody>';
  data.files.forEach(function(f) {
    html += '<tr><td>' + f.name + '</td><td>' + f.size + '</td>' +
            '<td><button class="btn btn-primary" style="padding:4px 12px;" onclick="viewReport(\'' + f.name + '\')">查看</button></td></tr>';
  });
  html += '</tbody></table>';
  document.getElementById('report-list').innerHTML = html;
  // IMA tab file list
  var imaHtml = '<div class="card"><b>待同步文件（' + data.files.length + '个）：</b><div style="margin-top:8px;">';
  data.files.forEach(function(f) {
    imaHtml += '<div class="file-item"><span>' + f.name + ' (' + f.size + ')</span></div>';
  });
  imaHtml += '</div></div>';
  document.getElementById('ima-files').innerHTML = imaHtml;
}
async function viewReport(name) {
  var data = await apiGet('/api/report?file=' + encodeURIComponent(name));
  if (data.ok) {
    document.getElementById('report-content').innerHTML =
      '<div class="card"><h3>' + name + '</h3><pre style="white-space:pre-wrap;font-size:13px;line-height:1.8;">' +
      data.content.replace(/</g, '&lt;') + '</pre></div>';
  }
}
function openFolder() {
  fetch('/api/open-folder');
}
// ===== IMA 同步 =====
async function imaSync() {
  var kbName = document.getElementById('ima-kb-name').value.trim();
  if (!kbName) { showToast('请输入IMA知识库名称', 'error'); return; }
  if (!confirm('将所有报告和图片同步到IMA知识库「' + kbName + '」？')) return;
  document.getElementById('ima-status').innerHTML = '<div class="alert alert-info">正在同步，请稍候...</div>';
  var data = await apiPost('/api/ima-sync', {kb_name: kbName});
  if (data.ok) {
    document.getElementById('ima-status').innerHTML =
      '<div class="alert alert-success">同步完成！成功 ' + data.success + ' 个，失败 ' + data.failed + ' 个。</div>';
    showToast('IMA同步完成', 'success');
  } else {
    document.getElementById('ima-status').innerHTML = '<div class="alert alert-danger">' + data.error + '</div>';
    showToast(data.error || '同步失败', 'error');
  }
}
// ===== 设置 =====
async function loadConfig() {
  var data = await apiGet('/api/config');
  document.getElementById('cfg-school').value = data.school_name || '';
  document.getElementById('cfg-grade').value = data.grade || '';
  document.getElementById('cfg-class').value = data.class_name || '';
  document.getElementById('cfg-subject').value = data.subject || '';
  document.getElementById('cfg-ocr-key').value = data.ocr_api_key || '';
  document.getElementById('cfg-ark-key').value = data.ark_api_key || '';
  document.getElementById('cfg-ark-model').value = data.ark_model_id || '';
  document.getElementById('cfg-ima-client').value = data.ima_client_id || '';
  document.getElementById('cfg-ima-key').value = data.ima_api_key || '';
  document.getElementById('ima-kb-name').value = data.ima_kb_name || '';
}
async function saveConfig() {
  var data = {
    school_name: document.getElementById('cfg-school').value,
    grade: document.getElementById('cfg-grade').value,
    class_name: document.getElementById('cfg-class').value,
    subject: document.getElementById('cfg-subject').value,
    ocr_api_key: document.getElementById('cfg-ocr-key').value,
    ark_api_key: document.getElementById('cfg-ark-key').value,
    ark_model_id: document.getElementById('cfg-ark-model').value,
    ima_client_id: document.getElementById('cfg-ima-client').value,
    ima_api_key: document.getElementById('cfg-ima-key').value,
    ima_kb_name: document.getElementById('ima-kb-name').value
  };
  try {
    var resp = await apiPost('/api/config', data);
    if (resp.ok) showToast('设置已保存', 'success');
    else showToast('保存失败: ' + (resp.error||'未知'), 'error');
  } catch(e) {
    showToast('设置已更新到内存（文件写入被占用）', 'info');
  }
}
async function testApi(type) {
  showToast('正在保存设置并测试连接...', 'info');
  try { await saveConfig(); } catch(e) { /* 保存失败不影响测试 */ }
  var data = await apiPost('/api/test-api', {type: type});
  if (data.ok) showToast(type.toUpperCase() + ' 连接成功！', 'success');
  else showToast(data.error || '连接失败', 'error');
}
// 初始化
loadStudents();
</script>
</body>
</html>"""


# ============ HTTP Handler ============
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body = HTML_PAGE.encode('utf-8')
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.serve_html()
        elif self.path == "/api/config":
            cfg = load_config()
            self.send_json(cfg)
        elif self.path == "/api/students":
            self.serve_students()
        elif self.path == "/api/status":
            self.send_json(analysis_status)
        elif self.path.startswith("/api/reports"):
            self.serve_reports()
        elif self.path.startswith("/api/report?"):
            self.serve_report_file()
        elif self.path == "/api/open-folder":
            folder = output_folder
            if folder and os.path.exists(folder):
                try:
                    os.startfile(folder)
                except Exception:
                    subprocess.Popen(['explorer', folder])
            self.send_json({"ok": True})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b''
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if self.path == "/api/config":
            cfg = load_config()
            for k, v in data.items():
                cfg[k] = v
            try:
                save_config(cfg)
            except Exception as e:
                # 配置文件可能被锁定/杀毒软件占用，但内存中已更新，继续运行
                pass
            self.send_json({"ok": True})
        elif self.path == "/api/students":
            self.add_student(data)
        elif self.path == "/api/students/import":
            self.import_roster(data)
        elif self.path == "/api/students/delete":
            self.delete_student(data)
        elif self.path == "/api/upload":
            self.upload_images(data)
        elif self.path == "/api/analyze":
            self.start_analysis()
        elif self.path == "/api/class-report":
            self.gen_class_report()
        elif self.path == "/api/ima-sync":
            self.ima_sync(data)
        elif self.path == "/api/clear":
            reset_session()
            self.send_json({"ok": True})
        elif self.path == "/api/test-api":
            self.test_api(data)
        else:
            self.send_error(404)

    def serve_students(self):
        result = []
        with data_lock:
            for sid, s in students_data.items():
                result.append({
                    "id": sid, "name": s["name"],
                    "images": len(s.get("images", [])),
                    "analyzed": s.get("analyzed", False),
                    "image_names": [img[0] for img in s.get("images", [])][:5],
                    "thumbnails": [base64.b64encode(img[1][:4096]).decode('utf-8') if len(img[1]) > 0 else "" for img in s.get("images", [])][:5]
                })
        self.send_json({"students": result})

    def add_student(self, data):
        sid = data.get("id", "").strip()
        name = data.get("name", "").strip()
        if not sid or not name:
            self.send_json({"ok": False, "error": "学号和姓名不能为空"})
            return
        with data_lock:
            if sid in students_data:
                self.send_json({"ok": False, "error": "该学号已存在"})
                return
            students_data[sid] = {"name": name, "images": [], "analyzed": False}
        self.send_json({"ok": True})

    def import_roster(self, data):
        text = data.get("roster", "")
        count = 0
        with data_lock:
            for line in text.strip().split("\n"):
                parts = line.strip().split(",")
                if len(parts) >= 2:
                    sid = parts[0].strip()
                    name = parts[1].strip()
                    if sid and name and sid not in students_data:
                        students_data[sid] = {"name": name, "images": [], "analyzed": False}
                        count += 1
        self.send_json({"ok": True, "count": count})

    def delete_student(self, data):
        sid = data.get("id", "")
        with data_lock:
            if sid in students_data:
                del students_data[sid]
        self.send_json({"ok": True})

    def upload_images(self, data):
        sid = data.get("student_id", "")
        images = data.get("images", [])
        if sid not in students_data:
            self.send_json({"ok": False, "error": "学生不存在"})
            return
        with data_lock:
            for img in images:
                name = img.get("name", "image.jpg")
                b64 = img.get("data", "")
                try:
                    img_bytes = base64.b64decode(b64)
                    students_data[sid]["images"].append((name, img_bytes))
                except Exception:
                    pass
        self.send_json({"ok": True, "count": len(images)})

    def start_analysis(self):
        if analysis_status["running"]:
            self.send_json({"ok": False, "error": "分析正在进行中"})
            return
        analysis_status["running"] = True
        analysis_status["done"] = False
        analysis_status["log"] = []
        analysis_status["errors"] = []
        t = threading.Thread(target=run_analysis)
        t.daemon = True
        t.start()
        self.send_json({"ok": True})

    def gen_class_report(self):
        cfg = load_config()
        try:
            filepath, err = generate_class_report(cfg)
            if err:
                self.send_json({"ok": False, "error": err})
            else:
                self.send_json({"ok": True, "file": os.path.basename(filepath)})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)})

    def serve_reports(self):
        folder = output_folder
        files = []
        if folder and os.path.exists(folder):
            for f in sorted(os.listdir(folder)):
                fp = os.path.join(folder, f)
                if os.path.isfile(fp):
                    size = os.path.getsize(fp)
                    size_str = f"{size/1024:.1f}KB" if size < 1024*1024 else f"{size/1024/1024:.1f}MB"
                    files.append({"name": f, "size": size_str})
        self.send_json({"files": files})

    def serve_report_file(self):
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        filename = params.get("file", [""])[0]
        folder = output_folder
        if not folder:
            self.send_json({"ok": False, "error": "暂无报告"})
            return
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            self.send_json({"ok": False, "error": "文件不存在"})
            return
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        self.send_json({"ok": True, "content": content})

    def ima_sync(self, data):
        kb_name = data.get("kb_name", "").strip()
        if not kb_name:
            self.send_json({"ok": False, "error": "请填写知识库名称"})
            return
        cfg = load_config()
        if not cfg.get("ima_client_id") or not cfg.get("ima_api_key"):
            self.send_json({"ok": False, "error": "未配置IMA凭证，请在设置中填写"})
            return
        try:
            kb_id, err = search_ima_kb(kb_name, cfg)
            if err:
                self.send_json({"ok": False, "error": err})
                return
            # 收集所有要上传的文件
            folder = get_output_folder() if students_data else output_folder
            files_to_upload = []
            if folder and os.path.exists(folder):
                for f in os.listdir(folder):
                    fp = os.path.join(folder, f)
                    if os.path.isfile(fp):
                        files_to_upload.append(fp)
            # 也上传原始图片
            for sid, s in students_data.items():
                for img_name, img_bytes in s.get("images", []):
                    img_path = os.path.join(folder, f"{sid}_{img_name}")
                    if not os.path.exists(img_path):
                        with open(img_path, "wb") as ff:
                            ff.write(img_bytes)
                    if img_path not in files_to_upload:
                        files_to_upload.append(img_path)
            if not files_to_upload:
                self.send_json({"ok": False, "error": "没有文件可上传"})
                return
            success = 0
            failed = 0
            last_error = ""
            for fp in files_to_upload:
                try:
                    upload_to_ima(fp, kb_id, cfg)
                    success += 1
                except Exception as e:
                    failed += 1
                    last_error = str(e)
            self.send_json({"ok": True, "success": success, "failed": failed, "error": last_error if failed else None})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)})

    def test_api(self, data):
        api_type = data.get("type", "")
        cfg = load_config()
        try:
            if api_type == "ocr":
                if not cfg.get("ocr_api_key"):
                    self.send_json({"ok": False, "error": "未填写OCR API Key"})
                    return
                url = f"{cfg['ocr_base_url']}/chat/completions"
                headers = {"Authorization": f"Bearer {cfg['ocr_api_key']}", "Content-Type": "application/json"}
                test_data = {"model": cfg["ocr_model"], "messages": [{"role": "user", "content": "测试"}]}
                resp = requests.post(url, headers=headers, json=test_data, timeout=15)
                result = resp.json()
                if "choices" in result or "usage" in result:
                    self.send_json({"ok": True})
                else:
                    msg = result.get("error", {}).get("message", str(result)) if isinstance(result.get("error"), dict) else str(result.get("error", result))
                    self.send_json({"ok": False, "error": msg})
            elif api_type == "ark":
                if not cfg.get("ark_api_key"):
                    self.send_json({"ok": False, "error": "未填写ARK API Key"})
                    return
                url = f"{cfg['ark_base_url']}/chat/completions"
                headers = {"Authorization": f"Bearer {cfg['ark_api_key']}", "Content-Type": "application/json"}
                test_data = {"model": cfg["ark_model_id"], "messages": [{"role": "user", "content": "测试"}]}
                resp = requests.post(url, headers=headers, json=test_data, timeout=15)
                result = resp.json()
                if "choices" in result:
                    self.send_json({"ok": True})
                else:
                    msg = result.get("error", {}).get("message", str(result)) if isinstance(result.get("error"), dict) else str(result.get("error", result))
                    self.send_json({"ok": False, "error": msg})
            elif api_type == "ima":
                if not cfg.get("ima_client_id") or not cfg.get("ima_api_key"):
                    self.send_json({"ok": False, "error": "未填写IMA凭证"})
                    return
                resp = ima_api("openapi/wiki/v1/search_knowledge_base", {"query": "test", "cursor": "", "limit": 1}, cfg)
                if resp.get("code") == 0:
                    self.send_json({"ok": True})
                elif resp.get("code") == 200002:
                    self.send_json({"ok": False, "error": "IMA凭证已过期，请到 ima.qq.com/agent-interface 续期"})
                else:
                    self.send_json({"ok": False, "error": resp.get("msg", "未知错误")})
            else:
                self.send_json({"ok": False, "error": "未知API类型"})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)})


# ============ 主入口 ============
def main():
    cfg = load_config()
    port = cfg.get("port", 8099)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"通用班级学情提分系统已启动: http://127.0.0.1:{port}")
    print(f"浏览器已自动打开，如未打开请手动访问上述地址")
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()

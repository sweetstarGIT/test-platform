"""包解析服务 - 自动识别 APK/RPK 包名"""
import os
import subprocess
import re
import zipfile
import json


def parse_package_name(file_path: str) -> str:
    """自动解析包名，支持 APK 和 RPK"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".apk":
        return _parse_apk(file_path)
    elif ext == ".rpk":
        return _parse_rpk(file_path)
    return ""


def _parse_apk(file_path: str) -> str:
    """用 aapt 解析 APK 包名"""
    try:
        result = subprocess.run(
            ["aapt", "dump", "badging", file_path],
            capture_output=True, text=True, timeout=30
        )
        match = re.search(r"package: name='([^']+)'", result.stdout)
        if match:
            return match.group(1)
    except FileNotFoundError:
        # aapt 不在 PATH 中，尝试从 APK 的 AndroidManifest.xml 中提取包名
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                if 'AndroidManifest.xml' in zf.namelist():
                    data = zf.read('AndroidManifest.xml')
                    # Binary XML 中包名以 UTF-16LE 编码出现在 manifest 标签的 package 属性中
                    # 搜索常见的包名模式（com.xxx.xxx 或 org.xxx.xxx）
                    text = data.decode('utf-8', errors='ignore')
                    match = re.search(r'(com\.[a-zA-Z0-9_.]+|org\.[a-zA-Z0-9_.]+|cn\.[a-zA-Z0-9_.]+)', text)
                    if match:
                        return match.group(1)
        except Exception:
            pass
    except Exception:
        pass

    # 兜底：从文件名提取
    basename = os.path.splitext(os.path.basename(file_path))[0]
    return basename


def _parse_rpk(file_path: str) -> str:
    """解析 RPK 包名（快应用包）"""
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            # RPK 里通常有 manifest.json
            for name in zf.namelist():
                if name.endswith("manifest.json"):
                    with zf.open(name) as f:
                        manifest = json.loads(f.read())
                        return manifest.get("package", "")
    except Exception:
        pass

    # 兜底：从文件名提取
    basename = os.path.splitext(os.path.basename(file_path))[0]
    return basename


def get_file_type(filename: str) -> str:
    """获取文件类型"""
    ext = os.path.splitext(filename)[1].lower()
    return ext.lstrip(".")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则集转换工具：
从上游（如 blackmatrix7/ios_rule_script）抓取分流规则，
仅保留 DOMAIN 与 DOMAIN-SUFFIX 规则，
并利用 Mihomo 内置转换器编译为高性能二进制 .mrs 规则集。
"""

import os
import sys
import json
import gzip
import shutil
import zipfile
import platform
import argparse
import subprocess
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Tuple

UPSTREAM_BASE_URL = (
    "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Surge"
)
DEFAULT_OUTPUT_DIR = os.path.join("rule", "geosite")
DEFAULT_CONFIG_FILE = "rules.json"
MIHOMO_VERSION = "v1.19.31"


def download_with_retry(url: str, max_retries: int = 3, timeout: int = 30) -> bytes:
    """带重试机制的 HTTP 下载"""
    headers = {
        "User-Agent": "Mozilla/5.0 (RuleConverter/1.0; +https://github.com)"
    }
    req = urllib.request.Request(url, headers=headers)
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionResetError) as e:
            last_err = e
            if attempt < max_retries:
                print(f"    [!] 下载重试 ({attempt}/{max_retries}) {url}: {e}")
    raise RuntimeError(f"下载失败 ({url}): {last_err}")


def get_mihomo_executable(custom_path: Optional[str] = None) -> str:
    """获取可用的 mihomo 可执行文件路径，若本地未找到则自动下载对应平台的二进制"""
    if custom_path and os.path.isfile(custom_path):
        return custom_path

    # 1. 优先查找环境变量或系统 PATH
    env_bin = os.getenv("MIHOMO_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    which_path = shutil.which("mihomo")
    if which_path:
        return which_path

    # 2. 检查当前目录下是否有 mihomo 或 mihomo.exe
    local_names = ["mihomo", "mihomo.exe"]
    for name in local_names:
        if os.path.isfile(name):
            return os.path.abspath(name)

    # 3. 自动下载对应平台的 mihomo
    system = platform.system().lower()
    machine = platform.machine().lower()
    print(f"[*] 系统未检测到 mihomo 可执行文件，正在自动为 {system}-{machine} 下载...")

    is_windows = system == "windows"
    is_darwin = system == "darwin"
    is_linux = system == "linux"

    target_bin_name = "mihomo.exe" if is_windows else "mihomo"

    if is_windows:
        asset_name = f"mihomo-windows-amd64-compatible-{MIHOMO_VERSION}.zip"
    elif is_darwin:
        if "arm" in machine or "aarch64" in machine:
            asset_name = f"mihomo-darwin-arm64-{MIHOMO_VERSION}.gz"
        else:
            asset_name = f"mihomo-darwin-amd64-compatible-{MIHOMO_VERSION}.gz"
    elif is_linux:
        if "arm" in machine or "aarch64" in machine:
            asset_name = f"mihomo-linux-arm64-{MIHOMO_VERSION}.gz"
        else:
            asset_name = f"mihomo-linux-amd64-compatible-{MIHOMO_VERSION}.gz"
    else:
        raise RuntimeError(f"暂不支持的操作系统架构: {system}-{machine}")

    download_url = (
        f"https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VERSION}/{asset_name}"
    )
    print(f"    下载地址: {download_url}")
    data = download_with_retry(download_url, max_retries=3, timeout=60)

    temp_archive = os.path.join(".", "mihomo_download_tmp")
    with open(temp_archive, "wb") as f:
        f.write(data)

    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(temp_archive, "r") as zf:
            for item in zf.namelist():
                if item.endswith(".exe") or item == "mihomo":
                    extracted = zf.extract(item, ".")
                    if extracted != target_bin_name:
                        shutil.move(extracted, target_bin_name)
                    break
    elif asset_name.endswith(".gz"):
        with gzip.open(temp_archive, "rb") as gz:
            with open(target_bin_name, "wb") as out_f:
                shutil.copyfileobj(gz, out_f)
        os.chmod(target_bin_name, 0o755)

    if os.path.exists(temp_archive):
        os.remove(temp_archive)

    if not os.path.isfile(target_bin_name):
        raise RuntimeError("解压 mihomo 二进制失败")

    return os.path.abspath(target_bin_name)


def fetch_upstream_rule_content(
    rule_name: str, specified_file: Optional[str] = None, custom_url: Optional[str] = None
) -> Tuple[str, str]:
    """
    抓取上游规则内容。
    对于 Surge 目录规则：
    若有 _All.list 则优先使用 _All.list（包含完整域名），否则使用 .list。
    返回 (内容字符串, 使用的来源文件名或URL)
    """
    if custom_url:
        print(f"    抓取自定义 URL: {custom_url}")
        content = download_with_retry(custom_url).decode("utf-8", errors="replace")
        return content, custom_url

    candidate_files = []
    if specified_file:
        candidate_files = [specified_file]
    else:
        candidate_files = [f"{rule_name}_All.list", f"{rule_name}.list"]

    for filename in candidate_files:
        url = f"{UPSTREAM_BASE_URL}/{rule_name}/{filename}"
        try:
            data = download_with_retry(url, max_retries=2, timeout=20)
            return data.decode("utf-8", errors="replace"), filename
        except Exception:
            continue

    raise FileNotFoundError(f"无法在上游找到规则 {rule_name} 的任何候选文件: {candidate_files}")


def filter_domain_rules(content: str) -> List[str]:
    """
    仅保留 DOMAIN 与 DOMAIN-SUFFIX 规则。
    去除重复项并按字母升序排序。
    """
    rules_set = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(",")
        if len(parts) >= 2:
            rule_type = parts[0].strip().upper()
            domain = parts[1].strip()

            # 仅保留 DOMAIN 和 DOMAIN-SUFFIX
            if rule_type in ("DOMAIN", "DOMAIN-SUFFIX") and domain:
                rules_set.add(f"{rule_type},{domain}")

    return sorted(rules_set)


def convert_to_mrs(
    mihomo_bin: str,
    rules: List[str],
    output_path: str,
    temp_dir: str = ".tmp"
) -> bool:
    """使用 mihomo convert-ruleset 将规则编译为 .mrs 二进制"""
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    base_name = os.path.splitext(os.path.basename(output_path))[0]
    temp_txt_path = os.path.join(temp_dir, f"{base_name}.txt")

    with open(temp_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rules) + "\n")

    cmd = [
        mihomo_bin,
        "convert-ruleset",
        "domain",
        "text",
        temp_txt_path,
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    [x] 编译错误 (退出码 {result.returncode}):")
        if result.stderr:
            print(result.stderr)
        return False

    return True


def format_size(size_bytes: int) -> str:
    """格式化字节大小显示"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def main():
    parser = argparse.ArgumentParser(description="分流规则转换工具 (Surge list -> Mihomo .mrs)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="规则配置文件路径")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录 (默认 rule/geosite)")
    parser.add_argument("--mihomo-bin", default=None, help="自定义 mihomo 二进制路径")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[!] 找不到配置文件: {args.config}")
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        rule_configs: List[Dict[str, str]] = json.load(f)

    mihomo_bin = get_mihomo_executable(args.mihomo_bin)
    print(f"[*] 使用 Mihomo 编译器: {mihomo_bin}")
    print(f"[*] 输出目录: {os.path.abspath(args.output_dir)}\n")

    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp_build")
    os.makedirs(temp_dir, exist_ok=True)

    summary_records = []

    try:
        for idx, item in enumerate(rule_configs, start=1):
            upstream = item.get("upstream")
            target = item.get("target") or upstream
            custom_url = item.get("url")
            specified_file = item.get("file")
            desc = item.get("description", "")

            target_mrs_filename = f"{target}.mrs"
            target_mrs_path = os.path.join(args.output_dir, target_mrs_filename)

            print(f"[{idx}/{len(rule_configs)}] 处理: {upstream} -> {target_mrs_filename} ({desc})")

            try:
                content, source_desc = fetch_upstream_rule_content(
                    upstream, specified_file=specified_file, custom_url=custom_url
                )
                filtered_rules = filter_domain_rules(content)
                rule_count = len(filtered_rules)
                print(f"    来源: {source_desc} | 过滤提取出 DOMAIN / DOMAIN-SUFFIX 规则数: {rule_count}")

                if rule_count == 0:
                    print("    [!] 警告: 未提取到任何有效 DOMAIN 或 DOMAIN-SUFFIX 规则，跳过生成")
                    summary_records.append((target_mrs_filename, 0, "0 B", "无有效规则跳过"))
                    continue

                success = convert_to_mrs(mihomo_bin, filtered_rules, target_mrs_path, temp_dir=temp_dir)
                if success and os.path.isfile(target_mrs_path):
                    file_size = os.path.getsize(target_mrs_path)
                    formatted_size = format_size(file_size)
                    print(f"    [√] 成功生成: {target_mrs_path} ({formatted_size})\n")
                    summary_records.append((target_mrs_filename, rule_count, formatted_size, "成功"))
                else:
                    print(f"    [x] 生成失败: {target_mrs_path}\n")
                    summary_records.append((target_mrs_filename, rule_count, "-", "编译失败"))

            except Exception as e:
                print(f"    [x] 发生异常: {e}\n")
                summary_records.append((target_mrs_filename, 0, "-", f"错误: {e}"))

    finally:
        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    # 打印汇总表
    print("=" * 65)
    print(f"{'目标文件':<20} | {'规则数量':<10} | {'文件大小':<12} | {'状态'}")
    print("-" * 65)
    for target_file, count, size, status in summary_records:
        print(f"{target_file:<20} | {count:<10} | {size:<12} | {status}")
    print("=" * 65)


if __name__ == "__main__":
    main()

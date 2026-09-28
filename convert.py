#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则集转换工具（供 GitHub Actions 运行）：
从上游（如 blackmatrix7/ios_rule_script）抓取分流规则，
仅保留 DOMAIN 与 DOMAIN-SUFFIX 规则，
支持自动合并用户自定义的额外规则（如 config/cn-extra.txt），
调用 Mihomo 内置转换器编译为高性能二进制 .mrs 规则集并存入 rule/geosite/ 目录。
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Tuple, Set

UPSTREAM_BASE_URL = (
    "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Surge"
)
DEFAULT_OUTPUT_DIR = os.path.join("rule", "geosite")
DEFAULT_CONFIG_FILES = [
    os.path.join("config", "rules.json"),
    "rules.json"
]


def download_with_retry(url: str, max_retries: int = 3, timeout: int = 30) -> bytes:
    """带重试机制的 HTTP 下载"""
    headers = {
        "User-Agent": "Mozilla/5.0 (GitHubActions/RuleConverter; +https://github.com)"
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
    仅保留 DOMAIN 与 DOMAIN-SUFFIX 规则，并转换为 Mihomo 原生高性能紧凑格式：
    - DOMAIN-SUFFIX,example.com -> +.example.com
    - DOMAIN,example.com -> example.com
    Mihomo 编译原生紧凑格式时会采用极致压缩的 Trie 树存储，体积减少约 50%！
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

            if rule_type == "DOMAIN-SUFFIX" and domain:
                rules_set.add(f"+.{domain}")
            elif rule_type == "DOMAIN" and domain:
                rules_set.add(domain)

    return sorted(rules_set)


def parse_extra_rules(filepath: str) -> List[str]:
    """
    解析本地额外的自定义规则文件（如 config/cn-extra.txt）。
    支持格式：
    - +.example.com
    - example.com
    - .example.com
    - DOMAIN-SUFFIX,example.com
    - DOMAIN,example.com
    自动过滤注释和空行。
    """
    rules = []
    if not os.path.isfile(filepath):
        return rules

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            # 彻底去除整行与行内注释（支持 #, //, ;）
            line = line.split("#")[0].split("//")[0].split(";")[0].strip()
            if not line:
                continue

            if "," in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    t, d = parts[0].strip().upper(), parts[1].strip()
                    if t == "DOMAIN-SUFFIX" and d:
                        rules.append(f"+.{d}")
                    elif t == "DOMAIN" and d:
                        rules.append(d)
            else:
                if line.startswith("+."):
                    rules.append(line)
                elif line.startswith("."):
                    rules.append(f"+{line}")
                else:
                    rules.append(line)
    return rules


def find_extra_file(extra_setting: Optional[str], target_name: str, config_dir: str) -> Optional[str]:
    """定位额外规则文件的路径"""
    candidates = []
    if extra_setting:
        candidates.append(extra_setting)
        candidates.append(os.path.join(config_dir, extra_setting))
        candidates.append(os.path.join(".", extra_setting))

    # 默认按约定自动探测：config/{target}-extra.txt 或 {target}-extra.txt
    candidates.append(os.path.join(config_dir, f"{target_name}-extra.txt"))
    candidates.append(f"{target_name}-extra.txt")

    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


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


def clean_obsolete_rules(output_dir: str, valid_targets: Set[str]) -> List[str]:
    """清理 output_dir 中未在配置列表里的过时 .mrs 文件"""
    removed = []
    if not os.path.exists(output_dir):
        return removed

    for fname in os.listdir(output_dir):
        if fname.endswith(".mrs"):
            target_name = os.path.splitext(fname)[0]
            if target_name not in valid_targets:
                fpath = os.path.join(output_dir, fname)
                try:
                    os.remove(fpath)
                    removed.append(fname)
                    print(f"[*] 已自动清理过时规则文件: {fname}")
                except Exception as e:
                    print(f"[!] 清理过时文件失败 {fname}: {e}")
    return removed


def resolve_config_path(custom_path: Optional[str]) -> str:
    """寻找可用的 rules.json 配置文件路径"""
    if custom_path:
        if os.path.isfile(custom_path):
            return custom_path
        raise FileNotFoundError(f"找不到指定的配置文件: {custom_path}")

    for path in DEFAULT_CONFIG_FILES:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"找不到默认配置文件，尝试了: {DEFAULT_CONFIG_FILES}")


def main():
    parser = argparse.ArgumentParser(description="分流规则转换工具 (Surge list -> Mihomo .mrs)")
    parser.add_argument("--config", default=None, help="规则配置文件路径 (默认自动寻找 config/rules.json 或 rules.json)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录 (默认 rule/geosite)")
    parser.add_argument("--mihomo-bin", default="mihomo", help="Mihomo 二进制执行文件路径")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    config_dir = os.path.dirname(os.path.abspath(config_path))

    # 检查 mihomo 二进制是否有效
    mihomo_path = shutil.which(args.mihomo_bin) or (
        os.path.abspath(args.mihomo_bin) if os.path.isfile(args.mihomo_bin) else None
    )
    if not mihomo_path:
        print(f"[!] 未找到指定的 Mihomo 可执行文件: {args.mihomo_bin}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        rule_configs: List[Dict[str, str]] = json.load(f)

    # 收集当前合法的目标名称
    valid_targets = {
        item.get("target") or item.get("upstream")
        for item in rule_configs
        if item.get("target") or item.get("upstream")
    }

    print(f"[*] 配置文件: {config_path}")
    print(f"[*] 使用 Mihomo 编译器: {mihomo_path}")
    print(f"[*] 输出目录: {os.path.abspath(args.output_dir)}")
    print(f"[*] 当前配置目标规则: {sorted(valid_targets)}\n")

    # 自动清理已从配置中移除的旧 .mrs 文件
    cleaned = clean_obsolete_rules(args.output_dir, valid_targets)
    if cleaned:
        print(f"[*] 共清理 {len(cleaned)} 个过时规则文件: {', '.join(cleaned)}\n")

    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp_build")
    os.makedirs(temp_dir, exist_ok=True)

    summary_records = []

    try:
        for idx, item in enumerate(rule_configs, start=1):
            upstream = item.get("upstream")
            target = item.get("target") or upstream
            custom_url = item.get("url")
            specified_file = item.get("file")
            extra_setting = item.get("extra")
            desc = item.get("description", "")

            target_mrs_filename = f"{target}.mrs"
            target_mrs_path = os.path.join(args.output_dir, target_mrs_filename)

            print(f"[{idx}/{len(rule_configs)}] 处理: {upstream} -> {target_mrs_filename} ({desc})")

            try:
                content, source_desc = fetch_upstream_rule_content(
                    upstream, specified_file=specified_file, custom_url=custom_url
                )
                filtered_rules = filter_domain_rules(content)
                upstream_count = len(filtered_rules)

                # 检查并解析自定义额外规则
                extra_file = find_extra_file(extra_setting, target, config_dir)
                extra_rules = []
                if extra_file:
                    extra_rules = parse_extra_rules(extra_file)
                    print(f"    [+] 合并额外自定义规则: {len(extra_rules)} 条 (来源: {extra_file})")

                # 合并上游规则与自定义额外规则
                final_rules = sorted(set(filtered_rules) | set(extra_rules))
                rule_count = len(final_rules)

                print(f"    来源: {source_desc} | 上游规则: {upstream_count} 条 | 最终去重合并总规则数: {rule_count}")

                if rule_count == 0:
                    print("    [!] 警告: 未提取到任何有效 DOMAIN 或 DOMAIN-SUFFIX 规则，跳过生成")
                    summary_records.append((target_mrs_filename, 0, "0 B", "无有效规则跳过"))
                    continue

                success = convert_to_mrs(mihomo_path, final_rules, target_mrs_path, temp_dir=temp_dir)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则集转换工具（供 GitHub Actions 运行）：
从上游（如 blackmatrix7/ios_rule_script）抓取分流规则，
支持通过 filter 字段灵活指定提取类型：
  - 域名类：DOMAIN, DOMAIN-SUFFIX（自动转为 Mihomo 原生 Trie 紧凑前缀格式）
  - IP 类：IP-CIDR（仅 IPv4）或 IP-CIDR,IP-CIDR6（IPv4 和 IPv6 双栈）
支持自动合并用户自定义的额外规则（如 config/*-extra.list），
调用 Mihomo 内置转换器编译为高性能二进制 .mrs 规则集。
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Tuple, Set, Union

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
    若指定了特定文件则优先使用，否则若有 _All.list 优先使用 _All.list，否则使用 .list。
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


def parse_filter_config(filter_val: Optional[Union[str, List[str]]]) -> Set[str]:
    """
    解析 filter 配置项，返回规范化大写的规则集合。
    配置在 rules.json 的 filter 参数中指定，例如：
    - "DOMAIN,DOMAIN-SUFFIX"
    - "IP-CIDR"
    - "IP-CIDR,IP-CIDR6"
    """
    if not filter_val:
        return set()
    if isinstance(filter_val, list):
        return {str(x).strip().upper() for x in filter_val if str(x).strip()}
    return {x.strip().upper() for x in str(filter_val).split(",") if x.strip()}


def load_rule_configs(raw_data: Union[Dict, List]) -> List[Dict]:
    """
    加载 rules.json 配置：
    按分组名（如 'domain'、'ipcidr'）直接赋予各条目 behavior，
    若提供扁平列表则直接读取。
    """
    flat_configs = []
    if isinstance(raw_data, dict):
        for group_name, items in raw_data.items():
            behavior = group_name.strip().lower()
            if isinstance(items, list):
                for item in items:
                    item_copy = dict(item)
                    item_copy["behavior"] = item_copy.get("behavior", behavior)
                    flat_configs.append(item_copy)
    elif isinstance(raw_data, list):
        for item in raw_data:
            flat_configs.append(dict(item))
    return flat_configs


def filter_rules(content: str, filter_types: Set[str], behavior: str) -> List[str]:
    """
    根据 filter_types 与 behavior 对规则内容进行精准过滤与标准化：
    - domain 行为：
        - DOMAIN-SUFFIX,example.com -> +.example.com
        - DOMAIN,example.com -> example.com
    - ipcidr 行为：
        - IP-CIDR,1.0.1.0/24,no-resolve -> 1.0.1.0/24
        - 当且仅当 filter 包含 IP-CIDR6 时才提取 IPv6，否则严格只保留 IPv4
    """
    rules_set = set()
    include_v4 = "IP-CIDR" in filter_types
    include_v6 = "IP-CIDR6" in filter_types

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(",")
        if len(parts) >= 2:
            rule_type = parts[0].strip().upper()
            payload = parts[1].strip()

            if behavior == "ipcidr":
                if rule_type == "IP-CIDR" and include_v4 and payload:
                    if ":" not in payload:
                        rules_set.add(payload)
                elif rule_type == "IP-CIDR6" and include_v6 and payload:
                    rules_set.add(payload)
            else:
                if rule_type in filter_types and payload:
                    if rule_type == "DOMAIN-SUFFIX":
                        rules_set.add(f"+.{payload}")
                    elif rule_type == "DOMAIN":
                        rules_set.add(payload)

    return sorted(rules_set)


def parse_extra_rules(filepath: str, filter_types: Set[str], behavior: str) -> List[str]:
    """
    解析本地额外的自定义规则文件（如 config/cn-extra.list）。
    与主过滤器保持完全一致的类型与注释处理。
    """
    if not os.path.isfile(filepath):
        return []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        clean_lines = []
        for raw_line in f:
            line = raw_line.strip()
            # 彻底去除整行与行内注释（支持 #, //, ;）
            line = line.split("#")[0].split("//")[0].split(";")[0].strip()
            if line:
                clean_lines.append(line)

    return filter_rules("\n".join(clean_lines), filter_types, behavior)


def find_extra_file(extra_setting: Optional[str], target_name: str, config_dir: str) -> Optional[str]:
    """定位额外规则文件的路径"""
    filename = extra_setting or f"{target_name}-extra.list"
    candidates = [
        os.path.join(config_dir, filename),
        filename,
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


def convert_to_mrs(
    mihomo_bin: str,
    behavior: str,
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
        behavior,
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


def clean_obsolete_rules(tracked_directories: Set[str], valid_target_files: Set[str]) -> List[str]:
    """清理输出目录中未在配置列表里的过时 .mrs 文件"""
    removed = []
    for out_dir in tracked_directories:
        if not os.path.exists(out_dir):
            continue
        for fname in os.listdir(out_dir):
            if fname.endswith(".mrs"):
                fpath = os.path.abspath(os.path.join(out_dir, fname))
                if fpath not in valid_target_files:
                    try:
                        os.remove(fpath)
                        removed.append(fname)
                        print(f"[*] 已自动清理过时规则文件: {fpath}")
                    except Exception as e:
                        print(f"[!] 清理过时文件失败 {fpath}: {e}")
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
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="默认输出目录 (默认 rule/geosite)")
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
        raw_config = json.load(f)
    rule_configs: List[Dict[str, str]] = load_rule_configs(raw_config)

    # 收集当前合法的目标文件与涉及的输出目录
    valid_target_files = set()
    tracked_directories = set()

    for item in rule_configs:
        target = item.get("target") or item.get("upstream")
        out_dir = item.get("dir") or args.output_dir
        tracked_directories.add(os.path.abspath(out_dir))
        valid_target_files.add(os.path.abspath(os.path.join(out_dir, f"{target}.mrs")))

    print(f"[*] 配置文件: {config_path}")
    print(f"[*] 使用 Mihomo 编译器: {mihomo_path}")
    print(f"[*] 默认输出目录: {os.path.abspath(args.output_dir)}")
    print(f"[*] 当前配置目标规则: {[item.get('target') for item in rule_configs]}\n")

    # 自动清理已从配置中移除的旧 .mrs 文件
    cleaned = clean_obsolete_rules(tracked_directories, valid_target_files)
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
            filter_val = item.get("filter")
            out_dir = item.get("dir") or args.output_dir
            behavior = (item.get("behavior") or "domain").strip().lower()
            filter_types = parse_filter_config(filter_val)
            filter_desc = ",".join(sorted(filter_types))

            target_mrs_filename = f"{target}.mrs"
            target_mrs_path = os.path.join(out_dir, target_mrs_filename)

            print(f"[{idx}/{len(rule_configs)}] 处理 ({behavior}) [{filter_desc}]: {upstream} -> {target_mrs_path}")

            try:
                content, source_desc = fetch_upstream_rule_content(
                    upstream, specified_file=specified_file, custom_url=custom_url
                )

                filtered_rules = filter_rules(content, filter_types, behavior)
                upstream_count = len(filtered_rules)

                # 检查并解析自定义额外规则
                extra_file = find_extra_file(extra_setting, target, config_dir)
                extra_rules = []
                if extra_file:
                    extra_rules = parse_extra_rules(extra_file, filter_types, behavior)
                    print(f"    [+] 合并额外自定义规则: {len(extra_rules)} 条 (来源: {extra_file})")

                # 合并上游规则与自定义额外规则
                final_rules = sorted(set(filtered_rules) | set(extra_rules))
                rule_count = len(final_rules)

                print(f"    来源: {source_desc} | 上游规则: {upstream_count} 条 | 最终去重合并总规则数: {rule_count}")

                if rule_count == 0:
                    print("    [!] 警告: 未提取到任何有效规则，跳过生成")
                    summary_records.append((target_mrs_filename, behavior, filter_desc, 0, "0 B", "无有效规则跳过"))
                    continue

                success = convert_to_mrs(mihomo_path, behavior, final_rules, target_mrs_path, temp_dir=temp_dir)
                if success and os.path.isfile(target_mrs_path):
                    file_size = os.path.getsize(target_mrs_path)
                    formatted_size = format_size(file_size)
                    print(f"    [√] 成功生成: {target_mrs_path} ({formatted_size})\n")
                    summary_records.append((target_mrs_filename, behavior, filter_desc, rule_count, formatted_size, "成功"))
                else:
                    print(f"    [x] 生成失败: {target_mrs_path}\n")
                    summary_records.append((target_mrs_filename, behavior, filter_desc, rule_count, "-", "编译失败"))

            except Exception as e:
                print(f"    [x] 发生异常: {e}\n")
                summary_records.append((target_mrs_filename, behavior, filter_desc, 0, "-", f"错误: {e}"))

    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    # 打印汇总表
    print("=" * 85)
    print(f"{'目标文件':<16} | {'类型':<8} | {'过滤器 (filter)':<22} | {'规则数量':<10} | {'文件大小':<10} | {'状态'}")
    print("-" * 85)
    for target_file, beh, f_desc, count, size, status in summary_records:
        print(f"{target_file:<16} | {beh:<8} | {f_desc:<22} | {count:<10} | {size:<10} | {status}")
    print("=" * 85)


if __name__ == "__main__":
    main()

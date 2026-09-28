# Mihomo MRS 分流规则集生成器

[![Convert Upstream Rules to MRS](https://github.com/${{ github.repository }}/actions/workflows/convert.yml/badge.svg)](https://github.com/${{ github.repository }}/actions/workflows/convert.yml)

本项目用于自动将上游分流规则（基于 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)）转换为 **Mihomo (Clash.Meta)** 原生二进制规则集格式（`.mrs`）。

在转换过程中，转换脚本会**严格过滤并仅保留 `DOMAIN` 与 `DOMAIN-SUFFIX` 规则**，去除无用的 IP-CIDR、USER-AGENT、PROCESS-NAME 等类型，生成体积极小、加载速度极快、内存开销极低的 Geosite 二进制规则文件。

---

## 规则列表与输出映射

所有编译生成的二进制规则文件均存放在 [`rule/geosite`](./rule/geosite) 目录下：

| 上游规则名称 (Upstream) | 生成文件 (Output) | 说明 | 规则类型 |
| :--- | :--- | :--- | :--- |
| **Apple** | [`rule/geosite/Apple.mrs`](./rule/geosite/Apple.mrs) | Apple 旗下各项服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **Binance** | [`rule/geosite/Binance.mrs`](./rule/geosite/Binance.mrs) | Binance 币安相关服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **Google** | [`rule/geosite/Google.mrs`](./rule/geosite/Google.mrs) | Google 旗下各项服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **Microsoft** | [`rule/geosite/Microsoft.mrs`](./rule/geosite/Microsoft.mrs) | Microsoft 微软相关服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **ChinaMax** | [`rule/geosite/cn.mrs`](./rule/geosite/cn.mrs) | 中国大陆境内域名集合 | DOMAIN, DOMAIN-SUFFIX |

---

## 在 Mihomo / Clash.Meta 中使用

### 1. 远程订阅方式 (推荐)

在 Mihomo 配置文件的 `rule-providers` 中配置，并将 format 设置为 `mrs`：

```yaml
rule-providers:
  geosite-apple:
    type: http
    behavior: domain
    format: mrs
    url: "https://raw.githubusercontent.com/<你的用户名>/<你的仓库名>/main/rule/geosite/Apple.mrs"
    path: ./ruleset/Apple.mrs
    interval: 86400

  geosite-binance:
    type: http
    behavior: domain
    format: mrs
    url: "https://raw.githubusercontent.com/<你的用户名>/<你的仓库名>/main/rule/geosite/Binance.mrs"
    path: ./ruleset/Binance.mrs
    interval: 86400

  geosite-google:
    type: http
    behavior: domain
    format: mrs
    url: "https://raw.githubusercontent.com/<你的用户名>/<你的仓库名>/main/rule/geosite/Google.mrs"
    path: ./ruleset/Google.mrs
    interval: 86400

  geosite-microsoft:
    type: http
    behavior: domain
    format: mrs
    url: "https://raw.githubusercontent.com/<你的用户名>/<你的仓库名>/main/rule/geosite/Microsoft.mrs"
    path: ./ruleset/Microsoft.mrs
    interval: 86400

  geosite-cn:
    type: http
    behavior: domain
    format: mrs
    url: "https://raw.githubusercontent.com/<你的用户名>/<你的仓库名>/main/rule/geosite/cn.mrs"
    path: ./ruleset/cn.mrs
    interval: 86400

rules:
  - RULE-SET,geosite-binance,DIRECT
  - RULE-SET,geosite-apple,DIRECT
  - RULE-SET,geosite-microsoft,DIRECT
  - RULE-SET,geosite-google,PROXY
  - RULE-SET,geosite-cn,DIRECT
  - MATCH,PROXY
```

> **提示**：如果在国内网络环境下直连 GitHub Raw 较慢，可以使用 jsDelivr CDN 加速地址：  
> `https://fastly.jsdelivr.net/gh/<你的用户名>/<你的仓库名>@main/rule/geosite/Apple.mrs`

---

### 2. 本地文件方式

若已将 `.mrs` 文件下载到本地配置目录：

```yaml
rule-providers:
  geosite-cn:
    type: file
    behavior: domain
    format: mrs
    path: ./ruleset/cn.mrs
```

---

## 运行与维护

### 方式一：GitHub Actions 手动运行 (Workflow Dispatch)

1. 打开项目的 GitHub 仓库页面。
2. 点击顶部的 **Actions** 标签页。
3. 在左侧列表中选择 **Convert Upstream Rules to MRS**。
4. 点击右侧的 **Run workflow** 按钮，选择分支并点击绿色的 **Run workflow**。
5. 转换完成后，Action 会自动将生成的 `.mrs` 文件提交并推送到仓库的 `rule/geosite/` 目录中。

> 此外，工作流默认设置了每日定时任务（UTC 22:00 / 北京时间 06:00），会自动与上游规则保持同步。

---

### 方式二：本地运行

本项目采用纯标准库编写转换脚本，无需安装额外的 Python 第三方包。

1. **克隆仓库**：
   ```bash
   git clone https://github.com/<你的用户名>/<你的仓库名>.git
   cd <你的仓库名>
   ```

2. **运行转换**：
   ```bash
   python convert.py
   ```
   > 脚本会自动检测操作系统与架构，若本地没有安装 `mihomo`，会自动拉取对应平台的最新版 `mihomo` 并完成编译转换。

3. **指定参数运行**（可选）：
   ```bash
   # 自定义输出目录或指定外部 mihomo 路径
   python convert.py --config rules.json --output-dir rule/geosite --mihomo-bin /usr/local/bin/mihomo
   ```

---

## 自定义添加新规则

如需添加更多规则，只需在 [`rules.json`](./rules.json) 中添加相应配置项即可：

```json
[
  {
    "upstream": "Telegram",
    "target": "Telegram",
    "description": "Telegram 域名规则"
  },
  {
    "upstream": "OpenAI",
    "target": "OpenAI",
    "description": "OpenAI 域名规则"
  }
]
```

- **`upstream`**: 上游规则分类名称（对应 `blackmatrix7/ios_rule_script` 中的规则目录名）。
- **`target`**: 编译输出的 `.mrs` 文件名（例如 `"cn"` 会生成 `cn.mrs`）。
- **`file`** *(可选)*: 指定上游文件名（默认优先探测 `{upstream}_All.list`，不存在则使用 `{upstream}.list`）。
- **`url`** *(可选)*: 自定义任意第三方规则列表的完整 URL。

---

## 鸣谢

- 规则数据源：[blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)
- 内核转换器：[MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo)

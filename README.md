# Mihomo MRS 分流规则集自动转换

本项目利用 **GitHub Actions** 自动将上游分流规则（基于 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)）转换为 **Mihomo (Clash.Meta)** 原生二进制规则集格式（`.mrs`）。

**所有规则集均由 GitHub Actions 在云端环境编译生成并自动推送到 `rule/geosite/` 目录，无需本地编译。**

在转换过程中，工作流会**严格过滤并仅保留 `DOMAIN` 与 `DOMAIN-SUFFIX` 规则**，剔除 IP-CIDR、USER-AGENT、PROCESS-NAME 等无关规则，生成加载速度极快、内存开销极低的 Geosite 二进制规则集。

---

## 规则列表与输出映射

在 GitHub Actions 运行后，生成的 `.mrs` 文件存放在 [`rule/geosite`](./rule/geosite) 目录下：

| 上游规则名称 (Upstream) | 生成文件 (Output) | 说明 | 过滤保留规则 |
| :--- | :--- | :--- | :--- |
| **Apple** | `rule/geosite/Apple.mrs` | Apple 旗下各项服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **Binance** | `rule/geosite/Binance.mrs` | Binance 币安相关服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **Google** | `rule/geosite/Google.mrs` | Google 旗下各项服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **Microsoft** | `rule/geosite/Microsoft.mrs` | Microsoft 微软相关服务域名 | DOMAIN, DOMAIN-SUFFIX |
| **ChinaMax** | `rule/geosite/cn.mrs` | 中国大陆全量境内域名集合 | DOMAIN, DOMAIN-SUFFIX |

---

## 如何在 GitHub 页面手动运行编译

1. 打开本项目在 GitHub 的仓库页面。
2. 点击仓库导航栏的 **Actions** 选项卡。
3. 在左侧列表中选择 **Build MRS Rulesets** 工作流。
4. 点击右侧的 **Run workflow** 下拉按钮，选择 `main` 分支并点击绿色的 **Run workflow**。
5. 稍等 1~2 分钟，GitHub Actions 编译完成后会自动将生成的 `.mrs` 文件提交并推送到本仓库的 `rule/geosite/` 目录下。

> **提示**：除了手动触发外，工作流默认设置了每日定时任务（北京时间每天早晨 06:00），会自动与上游规则保持同步。

---

## 在 Mihomo / Clash.Meta 中引用

### 远程订阅配置示例

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

---

## 自定义修改或添加规则

如需新增规则，只需在 [`rules.json`](./rules.json) 中添加相应配置项并提交即可：

```json
[
  {
    "upstream": "Telegram",
    "target": "Telegram",
    "description": "Telegram 域名规则"
  }
]
```

- **`upstream`**: 上游规则分类名称（对应 `blackmatrix7/ios_rule_script` 中的规则目录名）。
- **`target`**: 编译输出的 `.mrs` 文件名（例如 `"cn"` 会生成 `cn.mrs`）。
- **`file`** *(可选)*: 指定上游文件名（默认优先探测 `{upstream}_All.list`，不存在则使用 `{upstream}.list`）。
- **`url`** *(可选)*: 自定义任意规则源的完整 URL。

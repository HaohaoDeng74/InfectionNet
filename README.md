# InfectionNet

感染免疫多组学文献导航的独立静态 MVP。前端只读取仓库内的 `data/papers.json`，不在访问者浏览器中请求 Europe PMC，因此境内部署时不依赖境外实时 API。

## 功能

- 搜索标题、摘要、作者和 DOI；按年份与发表日期筛选。
- 一周内、脓毒症、微生物单细胞、感染免疫多组学专题。
- 文献详情包含摘要、PMID/PMCID/DOI 原文入口与结构化速读字段。
- 定时从 Europe PMC 更新真实论文元数据；人工核验的解释与索引快照分开维护。
- 可选的 GitHub Actions 自动上传至阿里云 OSS。

## 本地运行

```bash
python -m http.server 8000
```

打开 `http://localhost:8000`。刷新文献快照（需要运行环境可访问 Europe PMC）：

```bash
python scripts/update_literature.py
```

## 中国大陆部署：阿里云 OSS

1. 准备实名域名与阿里云中国大陆 OSS bucket；启用静态网站托管并绑定自有域名。
2. 按接入商流程办理 ICP 备案，再配置 HTTPS 证书和 DNS。若网站内容被归入需要前置审批的行业，先向接入商与属地通信管理部门核实分类。
3. 获得备案号后，将其填入 `data/site-config.json` 的 `icp_record`（页面会将编号链接到工信部备案系统）。本地安装 `oss2`，设置 `OSS_ENDPOINT`、`OSS_BUCKET`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 后执行：

   ```bash
   python -m pip install oss2
   python scripts/deploy_oss.py
   ```

4. 部署后，从至少两家中国大陆网络（固定宽带和移动网络）实测首页、专题、搜索、详情、HTTPS、DNS 与加载时间。

自动发布：在 GitHub 仓库 Settings → Secrets and variables → Actions 配置 variables `DEPLOY_OSS=true`、`OSS_ENDPOINT`、`OSS_BUCKET`，以及 secrets `OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`。使用仅具备该 bucket 写对象权限的 RAM 子账号，并定期轮换密钥。此后源码变更、备案号配置变更、索引刷新和手动触发刷新完成后均可上传静态文件。

## GitHub Pages

可以将仓库根目录作为静态站点发布，适合预览或海外备用；本项目不自动开启 Pages。GitHub Pages 的文档说明了平台限额，但没有对中国大陆访问作出可用性保证，因此不能仅凭成功发布就判定大陆可达。

## 数据来源和更新

当前提交附带 5 条真实 API 记录的启动快照。完整 1,044 条刷新曾在本地验证成功；合并后首次运行更新工作流将重新生成完整限量快照。

`scripts/update_literature.py` 使用 Europe PMC REST API 的 `TITLE_ABS`、`FIRST_PDATE`、`SRC` 与 cursorMark 分页。只检索 MED、PMC 和 PPR 来源；每专题取近 730 个 UTC 日内按发表日期排序的最多 500 条，再加入人工指定论文。`query_stats` 记录每次的完整检索式、命中数、取回数和是否截断；页面也提示限量范围。这是文献发现快照，不是完整的两年文献库，也不保证“一周内”覆盖全部已发表论文。

“一周内”是快照中首次发表日期位于今天及此前 6 个 UTC 日的记录，跨所有专题计算。前端忽略所有持久化的 `week` 标签，排除未来、无效及缺失日期，跨日自动重算，因此旧快照不能让过期论文继续出现在该专题中。中国时间凌晨 00:00–07:59 仍按前一个 UTC 日计算。页面显示快照更新时间，超过 48 小时提示可能过期。`pubYear` 保留数据库的期刊/纸刊年份，不用来替代首次发表日期；Europe PMC 在原始日期不完整时可能推定 FIRST_PDATE，不能把它视为每篇论文均经人工核对的精确在线首发时间。

微生物单细胞检索使用明确的微生物单细胞短语与方法名，避免仅因论文同时提到感染与宿主单细胞就归入该专题。这种偏重精度的关键词检索可能漏掉未采用这些表达的论文，命中也仍需人工判断研究对象和质量。人工指定的经典论文可以超出 730 日范围。预印本与正式版本若使用不同 DOI，将分别保留。

去重依据规范化后的 PMID、PMCID、DOI 和带来源前缀的 ID，合并跨查询标签及 MED/PMC 的传递匹配；元数据优先采用 MED。`data/manual_records.json` 只维护稳定标识符、人工专题与结构化解读，每次都重新检索这些标识符以核实元数据，不把人工摘要伪装成数据库摘要。刷新失败时返回非零状态；HTTP 429/5xx、超时和无效响应有限重试，永久 HTTP 错误直接报错。任一专题异常空结果、分页中断、人工指定论文查无记录或 DOI 不符，均停止写入。所有查询和合并完成后才原子替换快照；旧文件及其时间戳在失败时保留。

GitHub Actions 每天北京时间 01:15 更新快照。**定时和手动工作流需先存在于默认分支，当前 Draft PR 本身不会启动每日刷新。** 合并后可从 Actions 手动运行；分支保护或工作流写权限可能阻止机器人推送，需要仓库维护者确认。刷新前执行离线测试；成功后提交快照，失败时保留 Actions 日志并阻止后续 OSS 发布。只有 main 分支刷新成功才允许触发可选 OSS 部署。GitHub Actions 的调度时间可能延迟，运行状态以 Actions 记录为准。

## 验证

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/frontend.test.cjs
python tests/live_europepmc.py  # 真实联网，只读，不覆盖快照
python scripts/update_literature.py  # 真实联网，成功后原子更新快照
```

离线测试覆盖过期标签、7 日边界、跨日失效、日期校验、分页、去重、重试、人工解读保留和失败保留旧文件。CI 将离线与真实 API 冒烟测试分开报告。详细审查发现、实测结果与未验证条件见 [docs/data-review.md](docs/data-review.md)。

## 访问与维护边界

- 源码仓库和线上托管是两件事；GitHub Pages、境外 Sites 域名或境外 API 都不能自动保证中国大陆稳定访问。
- 境内部署需要域名、备案主体、OSS bucket、HTTPS 证书和云账号权限；如适用，还需要主管部门前置审批。
- 已有的 ChatGPT Sites 站点有独立的 7 个版本，但其源码未连接到本仓库；本 MVP 不会覆盖或修改该站点。

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

`scripts/update_literature.py` 使用 Europe PMC REST API 检索近两年的脓毒症、微生物单细胞与感染免疫多组学记录，并单独抓取最近 7 天记录。检索式、字段映射和更新代码都在仓库内。专题标签来自检索式，不能视作文献质量评价。站点不下载或再分发付费全文；读者可跳转到出版商、PubMed、PMC 和 Europe PMC。经过人工核验的结构化解读位于 `data/manual_records.json`，自动刷新会保留该字段。

GitHub Actions 每天北京时间 01:15 更新快照，可从 Actions 页面手动触发。首次合并后请手动运行一次，检查 `data/papers.json` 是否成功更新，再配置境内存储发布所需变量和密钥。

## 访问与维护边界

- 源码仓库和线上托管是两件事；GitHub Pages、境外 Sites 域名或境外 API 都不能自动保证中国大陆稳定访问。
- 境内部署需要域名、备案主体、OSS bucket、HTTPS 证书和云账号权限；如适用，还需要主管部门前置审批。
- 已有的 ChatGPT Sites 站点有独立的 7 个版本，但其源码未连接到本仓库；本 MVP 不会覆盖或修改该站点。

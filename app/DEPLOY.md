# Parleywise 试点部署说明

目标：在试点机构内部的一台机器上跑起来，合规人员用浏览器访问。单机、单进程、SQLite，够 1–2 家试点、几个审核人用。

## 1. 准备
- 一台 macOS / Linux 机器，装好 uv。拷贝或 `git clone` 本仓库。
- `cp .env.example .env`，填 `LITELLM_BASE_URL`、`LITELLM_API_KEY` 和两个模型名。
- 管理员可在“模型管理”中，从网关 `/v1/models` 返回的模型中勾选聊天可用范围并设置默认模型，保存后立即生效。首次配置前仅开放 `PARLEYWISE_MODEL_CHEAP` 和 `PARLEYWISE_MODEL_STRONG`；两者还必须存在于网关列表。`PARLEYWISE_DEFAULT_CHAT_MODEL` 可设置首次配置前的默认模型，保存后以管理员设置为准。审查初筛、复核及会话压缩仍使用原有模型配置。请只勾选支持聊天的模型。
  **材料内容会发给这个网关背后的模型。** 试点机构如果要求数据不出内网，就把网关换成他们自己部署的模型服务（OpenAI 兼容接口即可），这一步要在试点协议里写清楚。
- `uv sync` 装依赖。

## 2. 建账号
默认关闭网页注册，账号可以用命令行管理：
```bash
uv run python -m app.auth add zhangsan 张三 --admin   # 管理员：可删除材料
uv run python -m app.auth add lisi 李四               # 审核人
uv run python -m app.auth list
uv run python -m app.auth passwd lisi                 # 改口令（会踢掉该用户已有会话）
uv run python -m app.auth disable lisi                # 停用
```
同一用户名连续 5 次口令错误锁 5 分钟。会话 12 小时过期。

如需邀请注册，可在本机 `.env` 设置强随机的 `PARLEYWISE_INVITE_CODE` 并重启服务；清空该配置即可关闭注册。注册账号均为普通审核人。**当前为单机构共享工作区，任何登录用户都可访问工作区内材料，邀请码只应发给获准查看全部材料的人。** 这是单进程试点机制，不是面向公网的账户或租户隔离方案；生产部署前须补充可靠的访问控制、限流和管理能力。用户可在个人资料中更改显示名和口令；修改口令后需重新登录。

## 3. 启动
```bash
PARLEYWISE_HOST=0.0.0.0 ./run.sh        # 局域网可访问，默认端口 8000
```
- 只能单进程运行，不要加 `--workers`。
- 建议在前面放机构现有的 HTTPS 反向代理（nginx 等），并在 `.env` 里设 `PARLEYWISE_SECURE_COOKIE=1`。
- 长期运行可交给 launchd / systemd，命令就是 `run.sh`。
- 健康检查：`GET /api/health`。

## 4. 数据
- 全部在 `PARLEYWISE_DATA_DIR`（默认 `app/data/parleywise.db`）。包括材料原文、AI 意见、复核决定和审计日志。
- 备份：`sqlite3 app/data/parleywise.db ".backup 备份文件.db"`，可在服务运行时执行。
- 导出某份材料的完整记录：复核页“导出 JSON”（含审计日志）。

## 5. 试点怎么做度量
1. 合规人员各自登录，正常审材料。
2. 一部分材料勾选“对照组”（建议随机分，约三分之一），这些材料审核人看不到 AI 意见。
3. 每组完成 5 份以上后，度量页会显示两组用时对比；“AI 组每份人工补充问题”和对照组“AI 报了、审核人没标”用来盯漏审。

这些数字只能用于探索，不能单独证明节省了一半时间或没有增加漏审：目前分组由提交人选择，并非系统随机分配；材料复杂度和审核人经验也可能不同。有效时长由浏览器心跳估计，不能替代独立计时。人工补充数及按材料和规则对齐的差异只是线索，不等于经专家确认的漏报率；需要独立专家逐份复核，并记录材料与审核人情况。

## 已知局限
- 无 OCR，扫描件 PDF 和图片材料不能用。
- 两个人同时打开同一份材料会重复计时，试点时约定一份材料只由一人审。
- 账号没有网页管理界面，也没有单点登录。

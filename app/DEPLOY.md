# 试点部署说明

目标：在试点机构内部的一台机器上跑起来，合规人员用浏览器访问。单机、单进程、SQLite，够 1–2 家试点、几个审核人用。

## 1. 准备
- 一台 macOS / Linux 机器，装好 uv。拷贝或 `git clone` 本仓库。
- `cp .env.example .env`，填 `LITELLM_BASE_URL`、`LITELLM_API_KEY` 和两个模型名。
  **材料内容会发给这个网关背后的模型。** 试点机构如果要求数据不出内网，就把网关换成他们自己部署的模型服务（OpenAI 兼容接口即可），这一步要在试点协议里写清楚。
- `uv sync` 装依赖。

## 2. 建账号
没有网页注册，账号用命令行管理：
```bash
uv run python -m app.auth add zhangsan 张三 --admin   # 管理员：可删除材料
uv run python -m app.auth add lisi 李四               # 审核人
uv run python -m app.auth list
uv run python -m app.auth passwd lisi                 # 改口令（会踢掉该用户已有会话）
uv run python -m app.auth disable lisi                # 停用
```
同一用户名连续 5 次口令错误锁 5 分钟。会话 12 小时过期。

## 3. 启动
```bash
HIAGENT_HOST=0.0.0.0 ./run.sh        # 局域网可访问，默认端口 8000
```
- 只能单进程运行，不要加 `--workers`。
- 建议在前面放机构现有的 HTTPS 反向代理（nginx 等），并在 `.env` 里设 `HIAGENT_SECURE_COOKIE=1`。
- 长期运行可交给 launchd / systemd，命令就是 `run.sh`。
- 健康检查：`GET /api/health`。

## 4. 数据
- 全部在 `HIAGENT_DATA_DIR`（默认 `app/data/hiagent.db`）。包括材料原文、AI 意见、复核决定和审计日志。
- 备份：`sqlite3 app/data/hiagent.db ".backup 备份文件.db"`，可在服务运行时执行。
- 导出某份材料的完整记录：复核页“导出 JSON”（含审计日志）。

## 5. 试点怎么做度量
1. 合规人员各自登录，正常审材料。
2. 一部分材料勾选“对照组”（建议随机分，约三分之一），这些材料审核人看不到 AI 意见。
3. 每组完成 5 份以上后，度量页会显示两组用时对比；“AI 组每份人工补充问题”和对照组“AI 报了、审核人没标”用来盯漏审。

## 已知局限
- 无 OCR，扫描件 PDF 和图片材料不能用。
- 两个人同时打开同一份材料会重复计时，试点时约定一份材料只由一人审。
- 账号没有网页管理界面，也没有单点登录。

# 拣选路线改造复测

配对实验分析的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹，以及路线方向盲态试验。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery、Trial/TrialBatch。

## 盲态试验
`#/trials` 提供路线方向盲态试验，避免提前暴露甲乙与原/新路线的对应：

- 测时阶段按盲码采集：每行 `blind_code / team / arm(甲或乙) / seconds`，每码一条甲、一条乙，不含任何原/新路线字样。此类批次以 `kind=blind` 正常接入数据页，`kind=business` 的原始前后测批次保持原样，普通复测照常运行。
- 分析员或保管员创建试验，小组与置信度在创建时冻结。盲态保管员（`custodian`）逐批把盲态批次纳入试验，并封存该批 `甲/乙 → 原/新` 方向；方向以 AES-GCM 密文入库（`BLIND_SEAL_SECRET` 派生密钥），接口、下载、审计和错误信息只出现密文摘要。
- 盲态计算按批次运行，只报告甲/乙差异；盲态结果不可批准、不可分发，也不出现在普通结果复核列表。
- 保管员揭盲后，服务端在单事务内按各批自己的封存方向分别还原，再合并正式结论——不会对整批统一翻转。揭盲同时冻结纳入批次与参数，之后新导入的批次不进入该结论。
- 仅保管员可揭盲；分析员与业务负责人的越权请求返回 403。揭盲幂等：重复请求沿用同一正式结论、不新建记录；中断的事务整体回滚，重试从原进度继续。
- 每个盲态任务携带试验轮次围栏：登记新批次或揭盲会递增轮次，早于轮次的盲态计算会被判废，不会覆盖封存范围或正式结论。

注意：盲码保护的是甲乙与原/新路线的对应关系。若某人同时掌握可逐人比对的原始前后测，仍可能按数值推断方向，因此测时端应直接按盲码采集；盲态阶段不得批准与分发，正式判断以揭盲结论为准。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18205；后端文档 http://localhost:19205/docs；数据库宿主端口 17705。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发；可发起盲态计算|
|viewer@demo.local|Demo123!|只读与下载|
|custodian@demo.local|Demo123!|盲态试验纳入盲态批次、封存方向与揭盲；其余与观察员相同的只读权限|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer/custodian；JobStatus：queued/running/completed/failed；JobMode：normal/blind/formal；TrialStatus：blind/unblinding/unblinded。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=pick-paired；FRONTEND_PORT=18205；BACKEND_PORT=19205；DB_PORT=17705；DB_PASSWORD、JWT_SECRET 见 .env.example；`BLIND_SEAL_SECRET` 独立用于封存映射加密（本地默认回退到 JWT_SECRET，生产必须单独配置且妥善备份，丢失将无法揭盲）；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 测试与验收
算法单测与盲态试验验收脚本在 `backend/tests/`：

```bash
cd backend
DATABASE_URL=sqlite:////tmp/blind-test.db REDIS_URL=redis://127.0.0.1:1/0 SEED_DEMO=false python tests/test_blind_trial.py
```

盲态脚本覆盖：普通复测照常运行、盲态接入字段校验、方向登记权限、方向不回显、盲态结果只报甲乙、禁止批准与分发、越权揭盲被拒、各批方向分别还原、封存后新导入不进入结论、旧盲态计算不覆盖正式结论、揭盲中断回滚与幂等重试。需要 Python 3.12、FastAPI、SQLAlchemy、NumPy、SciPy、cryptography、httpx。脚本仅用 SQLite，Redis 指向不可达地址以免等待队列连接。Docker/API/页面验收另行记录，不与算法单测混用。

所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。

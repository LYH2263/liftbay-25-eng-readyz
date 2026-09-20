# LiftBay

电梯派梯：同向优先与楼层距离评分，轿厢满员拒绝派工。

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4200 |
| API | http://localhost:9200 |
| API 文档 | http://localhost:9200/docs |
| Postgres | localhost:5443 |

健康检查：见下方「探针（liveness / readiness）」一节。

## 探针（liveness / readiness）

编排系统必须区分两个探针，避免把"进程在、库挂了"当成可接派工流量：

| 探针 | 路径 | 语义 | 探测数据库 | 可用于流量接入 |
| --- | --- | --- | --- | --- |
| 存活 liveness | `GET /api/health` | 仅表示进程能响应 | 否 | 否，只决定是否重启 |
| 就绪 readiness | `GET /api/readyz` | 进程能响应 **且** 数据库可连接、业务表可用 | 是（只读轻量查询 `SELECT 1 FROM buildings LIMIT 1`） | 是 |

就绪探测只做只读查询：不创建呼梯、不写派工回放。探测超时由
`READY_PROBE_TIMEOUT`（默认 2 秒）控制：数据库不可用或墙钟超时即判定未就绪。

存活探针（任何时候，即使数据库宕机）：

```http
GET /api/health → 200
{"status":"ok"}
```

就绪成功：

```http
GET /api/readyz → 200
{"status":"ready"}
```

数据库不可用 / 探测超时：

```http
GET /api/readyz → 503
{"status":"not_ready","reason":"db_unavailable"}   // 或 "db_timeout"
```

正文含稳定字段 `status=not_ready` 与简要 `reason`（`db_unavailable` /
`db_timeout`），便于探针脚本与告警解析。

编排建议：livenessProbe 指向 `/api/health`；readinessProbe（K8s）或负载均衡
健康检查指向 `/api/readyz`。`docker-compose.yml` 的 api 服务已用 readyz 配置
容器 healthcheck。

手动验证（模拟数据库故障时，存活仍成功、就绪失败）：

```bash
docker compose up -d
docker compose stop db                 # 模拟数据库宕机
curl -i http://localhost:9200/api/health   # 仍为 200
curl -i http://localhost:9200/api/readyz   # 503 {"status":"not_ready",...}
docker compose start db                # 恢复后 readyz 回到 200
```

## 页面

- `/buildings` — 楼栋
- `/cars` — 轿厢
- `/calls` — 呼梯
- `/dispatch` — 派工
- `/replay` — 回放
- `/congestion` — 拥堵

## 使用说明

1. 查看楼栋与轿厢状态。
2. 在呼梯页登记请求，在派工页按评分分配轿厢。
3. 回放页查看派工轨迹，拥堵页查看高峰楼层。

## 开发与测试

```bash
docker compose exec api pytest -q
```

`tests/test_probes.py` 用测试替身模拟数据库连接失败与阻塞超时（无需真实
Postgres），断言：

- 连接失败 → readyz 返回 503、`status=not_ready`、`reason=db_unavailable`；
- 探测超时 → readyz 返回 503、`reason=db_timeout`；
- 上述两种情况下 `/api/health` 仍返回 200；
- 探测 SQL 全部为只读（SELECT/SET），不创建呼梯、不写派工回放。

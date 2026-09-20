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

健康检查探针：

| 路径 | 语义 | 用途 |
| --- | --- | --- |
| `GET /api/health` | **存活探针 liveness**：仅表示进程能响应 HTTP，**不探测数据库**。库挂了也返回 200。 | 判断进程是否需要重启 |
| `GET /api/readyz` | **就绪探针 readiness**：进程存活**且数据库可连接**才返回 200；库不可用或探测超时返回 503。 | 判断是否可以接入/恢复派工流量 |

编排层（K8s/负载均衡）应把两者分开配置：livenessProbe 用 `/api/health`，
readinessProbe 用 `/api/readyz`。避免把"进程在、库挂了"当成可接派工流量。

示例响应：

```bash
# 就绪
$ curl -i http://localhost:9200/api/readyz
HTTP/1.1 200 OK
{"status":"ready","checks":{"database":"ok"}}

# 数据库不可连接（连接被拒 / 超时）
$ curl -i http://localhost:9200/api/readyz
HTTP/1.1 503 Service Unavailable
{"status":"not_ready","checks":{"database":"database unavailable: OperationalError: connection refused"}}

# 探测超时（超过 DB_READY_TIMEOUT，默认 2 秒）
HTTP/1.1 503 Service Unavailable
{"status":"not_ready","checks":{"database":"database probe timeout after 2s"}}

# 存活探针：无论数据库是否可用，进程在就返回 200
$ curl http://localhost:9200/api/health
{"status":"ok"}
```

`/api/readyz` 的数据库检查是轻量只读查询 `SELECT 1 FROM buildings LIMIT 1`
（建筑物表可查即可，空表也算就绪），**不创建呼梯、不写派工回放**。
相关配置（环境变量）：

- `DB_READY_TIMEOUT`（默认 `2` 秒）：就绪探测整体超时，超时返回 `not_ready`。
- `DB_CONNECT_TIMEOUT`（默认 `2` 秒）：数据库驱动层的建连超时。

> 进程启动时若数据库暂不可用，服务仍会启动（liveness 成功、readiness 失败），
> 数据库恢复后探针自动转为就绪，无需重启进程。

docker compose 已为 api 服务配置基于 `/api/readyz` 的 healthcheck；
在 Kubernetes 中建议分别配置：

```yaml
livenessProbe:
  httpGet: { path: /api/health, port: 9200 }
readinessProbe:
  httpGet: { path: /api/readyz, port: 9200 }
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

探针测试（无需真实 Postgres，用测试替身模拟连接失败与超时）：

```bash
docker compose exec api pytest tests/test_probes.py -q
```

覆盖：数据库正常时 readyz 返回 ready 且探测 SQL 为只读；连接被拒、查询
报错、探测超时三种情况 readyz 返回 503 且 `status=not_ready`；同一时刻
health 仍返回 200；启动期数据库不可用时进程仍能启动。

手工复现（库不可用时 readyz 失败、health 成功）：

```bash
# 指向一个不存在的数据库地址启动，再分别请求两个探针
DATABASE_URL="postgresql+psycopg2://x:x@127.0.0.1:5999/liftbay" \
  uvicorn app.main:app --port 9200
curl -i http://localhost:9200/api/health   # 200 {"status":"ok"}
curl -i http://localhost:9200/api/readyz   # 503 {"status":"not_ready", ...}
```

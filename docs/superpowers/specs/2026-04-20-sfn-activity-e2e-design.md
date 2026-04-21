# 本地 e2e 走真 Step Functions（Plan A：预跑 Worker）

日期：2026-04-20
状态：approved，进入实施

## 背景

当前 `scripts/test_e2e.py` 绕过 Step Functions：手工 `docker compose run worker` + 顺序 `lam.invoke`。原因是 MiniStack 的 `ecs:runTask.sync` 集成只返回成功不真跑容器。

后果：SFN JSON 定义（`Map` 并发、`ResultPath`、`Retry`）在本地从未被真实验证。

## Plan B（Activity 模式）：实测不可行

原设计想把 RunWorker 改成 SFN Activity 模式，由一个本地 `activity_worker.py` 进程 poll `GetActivityTask` 后 `docker compose run worker`。动机是 Activity 比 ECS 集成底层，MiniStack 理应支持更完整。

实测结论（2026-04-20）：**MiniStack 的 SFN Activity 支持是半残的**。

| API | 实测结果 |
|---|---|
| `sfn.create_activity` | ✅ 工作 |
| `sfn.list_activities` | ✅ 工作 |
| `sfn.send_task_success` / `send_task_failure` | ✅ 工作（API 可达） |
| `sfn.get_activity_task` | ⚠️ 长轮询 60s 恒返回空 token（即使 execution 正在等这个 Activity） |
| State machine 含 Activity Resource + `start_execution` | ❌ runtime 不把任务派发到对应 activity 队列 |

换言之：**Activity CRUD 可用，runtime 不调度**。skill 文档中的 "Step Functions: State machine CRUD" 就是这个意思。

同时验证了 MiniStack **对 Lambda Task 的 SFN runtime 是完整的**：`start_execution` 后真实触发 Lambda、传递 Payload、推进到下一个 state、走完 `SUCCEEDED`。

## 最终方案：Plan A（预跑 Worker + 真 SFN）

```
test_e2e.py 流程：
1. 上传 S3 fixtures
2. 直接用 PgBatchManager 创建 batch（不走 Scheduler 以便控制时机）
3. docker compose run --rm worker  ← 填 face_embeddings / photos.status=completed
4. sfn.start_execution(photo-pipeline, input={batch_id, s3_keys, trace_ctx})
   ↓ SFN 自己跑：
     RunWorker (ecs:runTask.sync, MiniStack fake-success 秒过)
     GetPhotoIds (Lambda, 真跑) ← DB 已填，返回真实 photo_ids
     TagPhotos Map ×N (Lambda, 真并发, MaxConcurrency=10)
     VLMExtract Map ×N (Lambda, 真并发)
     MarkComplete (Lambda, 真跑)
5. sfn.describe_execution 轮询直到 SUCCEEDED (1s 间隔, 180s 超时)
6. 验证 DB（batch.status, face_count, photos.tags, vlm_result）
```

## 改动清单

| 文件 | 操作 |
|---|---|
| `scripts/test_e2e.py` | 重写：删手工 Lambda 链，改直接 batch + worker + `start_execution` + 轮询 |
| `services/scheduler/src/scheduler/handler.py` | `return body` 多带 `execution_arn`（通用改进；e2e 虽不使用，调用方收获已有信息） |

**保留**所有 terraform/state-machines/Makefile 原状 —— 不引入 Activity 相关代码。

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 是否绕过 Scheduler | e2e 绕过，直接建 batch + 手工 `start_execution` | MiniStack 下 Scheduler 立即启动 SFN 会抢跑在 worker 之前；绕过保证时序一致。Scheduler 另由 unit test 覆盖 |
| 保留 Scheduler 的 `execution_arn` 返回 | 保留 | 通用改进，不丢已有信息；其他调用方（如 UI/dashboard）将来受益 |
| 是否改 state machine JSON | 不改 | pipeline-local.json 原样；MiniStack 下 RunWorker fake-success 是已知行为，test_e2e 的"预跑 worker" 对齐这个行为 |
| Activity 方案代码 | 全部不保留 | 实测不可行，保留会误导 |
| LangSmith trace context 在 SFN 内传播 | 本次不修 | pre-existing 问题；另 PR 处理 |

## 覆盖到 / 覆盖不到

✅ 新覆盖（相对现在）：
- SFN JSON 语法正确性（start_execution 能解析并成功执行）
- Map state 的真实并发调度（MaxConcurrency=10）
- ResultPath / ResultSelector / ItemsPath 的 JSON 路径求值
- Lambda event payload 契约 = 真实 SFN 生成的（不是手工拼的）
- MarkComplete 作为 SFN 末态触发

❌ 仍不覆盖：
- RunWorker 的 ECS ContainerOverrides 参数传递（MiniStack fake-success，AWS Batch 未 IaC 化，留给真 AWS smoke）
- SFN 内 `Retry` 触发（Lambda 正常跑不失败）
- Scheduler → SFN 的端到端链（Scheduler 单测覆盖）

## 失败处理

- `_wait_for_execution` 轮询失败时 `get_execution_history(reverseOrder=True, maxResults=20)` 打印最近事件 + `taskFailedEventDetails` / `executionFailedEventDetails` / `lambdaFunctionFailedEventDetails`
- 超时阈值 180s（Worker 启动 + InsightFace 模型加载 ~90s + SFN 执行 ~10s，留出余量）

## 回滚

`git revert`。无 terraform state 副作用（不引入新资源），无遗留进程。

## Follow-up（另起 PR）

1. `terraform/batch.tf` + `aws.tfvars` 完整 AWS Batch 资源（当前 `pipeline-aws.json` 引用 `${GPU_JOB_QUEUE}` / `${JOB_DEFINITION}` 无定义）
2. `build_lambda_zip.sh` 加 `uv pip install --target`，否则真 AWS Lambda 启动因缺 psycopg2 / langsmith 崩溃
3. SFN 内 `langsmith_trace_context` 传播：`pipeline-{local,aws}.json` 在 Lambda Payload 加 `langsmith_trace_context.$: "$.langsmith_trace_context"`

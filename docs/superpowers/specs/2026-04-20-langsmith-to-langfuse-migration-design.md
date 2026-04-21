# LangSmith → Langfuse 完整迁移

日期：2026-04-20
状态：approved，进入实施

## 背景

项目当前用 LangSmith `@traceable` + `RunTree` 做全 Pipeline 分布式 trace 串联。
用户决定切换到 Langfuse（US Cloud）作为可观测性后端，一次性替换，不保留双写。

## 目标

1. 代码层完全去掉 `langsmith` 依赖；全量切到 `langfuse>=4`（OTEL-based SDK）
2. 保持"全 Pipeline 一棵完整 trace"的行为
3. 借机把真·SFN-driven e2e 的 trace 连通（修 pre-existing 的 SFN 不传 trace ID 问题）
4. 不改变外部接口（Scheduler 返回值、Lambda event 契约）形态，只改字段名

## 核心设计决策

| # | 决策 | 选择 |
|---|---|---|
| 1 | 分布式 trace 传播机制 | **确定性 trace_id + 显式 parent_observation_id**：Scheduler 用 `create_trace_id(seed=request_id)` + `get_current_observation_id()` 取自己 span_id；event payload 传两字段 `langfuse_trace_id` 和 `langfuse_parent_observation_id`；下游用 `@observe()` 的内置 kwarg 即可实现真·父子嵌套（UI 里 photo_pipeline 是 parent，下游 span 是 child） |
| 2 | SFN JSON 更新 | 每个 Lambda 的 `Parameters.Payload` 追加 `"langfuse_trace_id.$": "$.langfuse_trace_id"` + `"langfuse_parent_observation_id.$": "$.langfuse_parent_observation_id"`，两份（local / aws）同步改 |
| 3 | Lambda flush 策略 | **每次 handler 结束强制 `langfuse.flush()`**（trade 100-500ms 保 trace 不丢） |
| 4 | 环境变量安全 | 用户真实 key 仅入本地 `.env`（.gitignore），`.env.example` 只写占位符 |
| 5 | MiniStack 镜像依赖 | `docker/ministack.Dockerfile` 的 `pip install` 列表 `langsmith` → `langfuse`，需 `destroy + setup` 重建 |

**关于 `langfuse_parent_observation_id` 的凭据**：虽然文档页面只记录 `langfuse_trace_id` 一个 kwarg，但 `@observe` 源码 docstring 明确列出支持 `langfuse_parent_observation_id`（见 `langfuse/_client/observe.py`）。probe 实测（`scripts/probe_langfuse.py`）验证 REST API 返回 child.parent_observation_id 正确指向 root span。

## 共享库 API：`packages/common/src/common/tracing.py`

重写后暴露 3 个符号，保持调用方改动最小：

```python
from contextlib import contextmanager

from langfuse import get_client

def init_trace_id(seed: str) -> str:
    """Scheduler 用：基于 request_id 生成 32-hex 确定性 trace_id。
    未配 Langfuse 时返回空字符串（下游 kwargs_from_event 识别为无 trace）。"""
    try:
        return get_client().create_trace_id(seed=seed)
    except Exception:
        return ""

@contextmanager
def traced_handler():
    """所有 Lambda handler 用 with 包住 handler 体，保证退出前 flush。
    不 care 是否有 trace（未设 key 时 SDK no-op）。"""
    try:
        yield
    finally:
        try:
            get_client().flush()
        except Exception:
            pass  # flush 失败不影响业务返回

def kwargs_from_event(event: dict) -> dict:
    """从 event 里提取 langfuse_trace_id + langfuse_parent_observation_id
    打包成 @observe 友好的 kwargs。空串 / 缺失字段跳过，调用方 `**kwargs` 展开无副作用。"""
    kw = {}
    if event.get("langfuse_trace_id"):
        kw["langfuse_trace_id"] = event["langfuse_trace_id"]
    if event.get("langfuse_parent_observation_id"):
        kw["langfuse_parent_observation_id"] = event["langfuse_parent_observation_id"]
    return kw
```

### Event payload 中 `langfuse_trace_id` 的不变式

**始终存在**（可能为空字符串），以简化 SFN `"langfuse_trace_id.$": "$.langfuse_trace_id"`
取值路径的存在性假设。Scheduler 写入规则：
```python
trace_id = init_trace_id(request_id)  # "" if no Langfuse
sfn_input = {
    "batch_id": batch_id,
    "s3_keys": s3_keys,
    "langfuse_trace_id": trace_id,  # 始终有键，值可能为空
}
```

### 调用方 idiom

**Scheduler**：
```python
from langfuse import observe, get_client
from common.tracing import init_trace_id, traced_handler

@observe(name="photo_pipeline")
def _run(request_id, user_id, s3_keys):
    # 业务：create batch
    batch_id = ...

    # 取自己的 span_id，随 event payload 传给下游（让下游 span 作为 child）
    parent_obs_id = get_client().get_current_observation_id()
    sfn.start_execution(input=json.dumps({
        "batch_id": batch_id,
        "s3_keys": s3_keys,
        "langfuse_trace_id": init_trace_id(request_id),   # 也可以存到局部复用
        "langfuse_parent_observation_id": parent_obs_id,
    }))
    return {...}

def handler(event, context):
    with traced_handler():
        trace_id = init_trace_id(event["request_id"])
        return _run(event["request_id"], event["user_id"], event["s3_keys"],
                    langfuse_trace_id=trace_id)  # 固定 Scheduler 这个 @observe 挂到该 trace
```

**其他 4 个 Lambda**：
```python
from langfuse import observe
from common.tracing import traced_handler, kwargs_from_event

@observe(name="stage2_tag_photo")
def _tag_photo(photo_id): ...

def handler(event, context):
    with traced_handler():
        return _tag_photo(event["photo_id"], **kwargs_from_event(event))
```

## 数据流

```
user → Scheduler.handler(event)
  │  request_id = event["request_id"]
  │  trace_id = init_trace_id(request_id)         ← 确定性 trace_id
  │  @observe("photo_pipeline") wraps _run
  │  _run 内 parent_obs_id = get_current_observation_id()   ← Scheduler 自己的 span_id
  │  sfn.start_execution(input={
  │     batch_id, s3_keys,
  │     langfuse_trace_id: trace_id                ← 下游共享 trace
  │     langfuse_parent_observation_id: parent_obs_id  ← 下游作为 photo_pipeline child
  │  })
  ▼
SFN RunWorker (fake-succeeds after test_e2e.py 手动 stop_task)
  ▼
SFN GetPhotoIds → Lambda invoke with Payload {
     batch_id, langfuse_trace_id, langfuse_parent_observation_id
  }
  │  handler(event):
  │     with traced_handler():
  │        _get_photo_ids(batch_id, **kwargs_from_event(event))
  │  @observe("get_photo_ids") → trace_id + parent_obs_id → 真 child of photo_pipeline
  ▼
SFN TagPhotos Map (10 并发) → photo-tagger invoke × N:
  Payload = { photo_id, langfuse_trace_id, langfuse_parent_observation_id }
  @observe("stage2_tag_photo") × N spans，全都是 photo_pipeline 的 child
  ▼
VLMExtract Map → photo-vlm × N（同上）
  ▼
MarkComplete（同上）
```

Langfuse UI 结果：一棵 trace（name=`photo_pipeline`），root 是 `photo_pipeline` span，下面嵌套
`get_photo_ids` + N×`stage2_tag_photo` + N×`stage3_vlm_extract` + `mark_complete` 作为 children。

## 改动清单（16 个文件）

### 代码
- `packages/common/src/common/tracing.py` — 重写
- `services/scheduler/src/scheduler/handler.py` — 切 `@observe` + `init_trace_id`
- `services/{get_photo_ids,tagger,vlm_extractor,mark_complete}/src/.../handler.py` — 切 `@observe` + `traced_handler` + `kwargs_from_event`
- `scripts/test_e2e.py` — `langsmith_trace_context` → `langfuse_trace_id`；SFN input 传 `create_trace_id(seed=request_id)`

### 状态机
- `state-machines/pipeline-local.json` — 每个 Lambda Payload 加 `langfuse_trace_id.$`
- `state-machines/pipeline-aws.json` — 同上

### 依赖
- `pyproject.toml`（root）+ 5 个 service `pyproject.toml` — `langsmith` → `langfuse`
- `uv.lock` — `uv lock` 重新生成
- `docker/ministack.Dockerfile` — pip install list swap

### IaC / 配置
- `terraform/variables.tf` — 新增 `langfuse_public_key` / `langfuse_secret_key` / `langfuse_base_url`，删 `langsmith_api_key`
- `terraform/main.tf` — Lambda env 注入上述 3 个；删 LANGSMITH_* 相关逻辑
- `Makefile` — `setup` 传参换成 3 个 Langfuse var
- `.env.example` — LANGFUSE_* 占位符，注释 LANGSMITH_* 删除

### 文档
- `README.md` — 可观测性段、技术栈、`.env` 说明
- `CLAUDE.md` — 可观测性段、MiniStack 限制里 LANGSMITH 相关条目改写

## 错误处理

- **无 LANGFUSE_* key**：`get_client()` 返回 no-op client（SDK 内置行为），`@observe` 不产生 trace，`flush` 无操作。业务代码完全 transparent
- **Langfuse cloud 不可达**：`flush()` 内部重试 + timeout；`traced_handler` 的 `except Exception: pass` 保证业务返回不受影响
- **event 无 langfuse_trace_id**：`kwargs_from_event` 返回 `{}`，下游 `@observe` 创建独立 trace（退化，trace 可能断链但不报错）

## 测试

1. `uv run ruff check .` — lint
2. `tofu validate` — IaC
3. `make destroy && make setup` — rebuild ministack 镜像 + Lambda zip
4. `make test-e2e` — 跑通
5. 手动：Langfuse UI 检查一棵完整 trace，包含 N×Tag + N×VLM spans

## 不做的事（YAGNI）

- 不捕获 Scheduler 的具体 `parent_span_id` 传给下游（只共享 trace_id 已够用，Langfuse UI 平铺也可读）
- 不改动已 mock 的 tagger/vlm_extractor 的 `@observe` 类型（保持 `span`，不升级到 `generation` —— 未来接真 LLM 时再改）
- 不给 MiniStack 跑本地 Langfuse 实例（直接指向 US cloud）
- 不做双写（LangSmith + Langfuse 并存）—— 一次性切

## 回滚

`git revert` 单个 commit。`.env.example` 里 LANGFUSE_* 改回 LANGSMITH_*，重跑 `make destroy && make setup`。

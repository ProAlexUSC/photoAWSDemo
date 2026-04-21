from common.db import get_connection
from common.tracing import (
    attach_aws_runtime_context,
    kwargs_from_event,
    lambda_context_scope,
    traced_handler,
)
from langfuse import get_client, observe


@observe(name="get_photo_ids")
def _get_photo_ids(batch_id):
    attach_aws_runtime_context()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT photo_id FROM photos WHERE batch_id = %s ORDER BY photo_id",
            (batch_id,),
        )
        photo_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return photo_ids


# Map 容器 span 工厂：立即 end 的空 span，存在的目的是让下游 Map 子 Lambda 挂在它下面嵌套显示，
# 而不是平铺成 root 的兄弟。Langfuse 按 parent_observation_id 关联，与 span 时长无关。
def _make_container_span(name: str):
    @observe(name=name)
    def _span():
        return get_client().get_current_observation_id()

    return _span


_tag_container_span = _make_container_span("TagPhotos")
_vlm_container_span = _make_container_span("VLMExtract")


def handler(event, context):
    with traced_handler(), lambda_context_scope(context):
        kw = kwargs_from_event(event)
        batch_id = event["batch_id"]
        photo_ids = _get_photo_ids(batch_id=batch_id, **kw)

        # langfuse 关闭或 trace context 缺失时 id 是空串，下游退化为顶层 span，不报错
        if kw.get("langfuse_trace_id") and kw.get("langfuse_parent_observation_id"):
            tag_parent_id = _tag_container_span(**kw) or ""
            vlm_parent_id = _vlm_container_span(**kw) or ""
        else:
            tag_parent_id = ""
            vlm_parent_id = ""

        return {
            "batch_id": batch_id,
            "photo_ids": photo_ids,
            "tag_parent_id": tag_parent_id,
            "vlm_parent_id": vlm_parent_id,
        }

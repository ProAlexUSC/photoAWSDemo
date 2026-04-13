import os


def is_local() -> bool:
    """仅用于非 AWS 逻辑分支（如跳过 Batch submit）。
    boto3 endpoint 切换由 AWS_ENDPOINT_URL 环境变量处理，与此函数无关。
    """
    return os.environ.get("LOCAL_DEV", "true").lower() == "true"


def get_database_url() -> str:
    """读取 DATABASE_URL 环境变量。未设置时抛 KeyError。"""
    return os.environ["DATABASE_URL"]

FROM nahuelnucera/ministack

# 基础镜像升级为 Alpine + Python 3.12，不自带 pip；先 ensurepip 再装依赖（走 BFSU PyPI 镜像）
RUN python -m ensurepip && \
    python -m pip install --no-cache-dir --break-system-packages \
        -i https://mirrors.bfsu.edu.cn/pypi/web/simple \
        psycopg2-binary boto3 langfuse

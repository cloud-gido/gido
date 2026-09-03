# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0

# 默认可用 DaoCloud 同步层（国内常见 hub 超时）；直连 Hub 时构建加: --build-arg PY_IMAGE=python:3.9-slim
ARG PY_IMAGE=docker.m.daocloud.io/library/python:3.9-slim
FROM ${PY_IMAGE}

# 国内默认清华源；GitHub Actions / 海外构建请覆盖为官方源，否则易 403：
#   --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# pip 默认 15s 超时，PyPI 抖动时会在 Collecting 阶段以 UNKNOWN_ERROR (exit 2) 中断
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=15

WORKDIR /app
COPY requirements.txt .

# pip 26.1+ 需要 Python 3.10；3.9 镜像钉在 <26.1。
# pip 23 遇到 files.pythonhosted.org ReadTimeoutError 会直接 exit 2，必须在 shell 层整次重试。
RUN set -eux; \
    python -m pip install --upgrade "pip>=24.3,<26.1" -i "${PIP_INDEX_URL}"; \
    i=1; \
    while [ "$i" -le 5 ]; do \
      python -m pip install --no-cache-dir --prefer-binary \
        --timeout 120 --retries 15 \
        -r requirements.txt -i "${PIP_INDEX_URL}" && break; \
      echo "pip install failed (attempt ${i}/5), retry in 15s..."; \
      i=$((i + 1)); \
      [ "$i" -le 5 ] || exit 1; \
      sleep 15; \
    done

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
         "https://dl.k8s.io/release/v1.29.14/bin/linux/${ARCH}/kubectl" \
         -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
COPY . .
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["sh", "-c", "python init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8001"]

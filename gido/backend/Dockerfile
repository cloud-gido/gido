# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0

# 默认可用 DaoCloud 同步层（国内常见 hub 超时）；直连 Hub 时构建加: --build-arg PY_IMAGE=python:3.9-slim
ARG PY_IMAGE=docker.m.daocloud.io/library/python:3.9-slim
FROM ${PY_IMAGE}

# 国内默认清华源；GitHub Actions / 海外构建请覆盖为官方源，否则易 403：
#   --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt -i "${PIP_INDEX_URL}"

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

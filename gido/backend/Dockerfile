

# 默认可用 DaoCloud 同步层（国内常见 hub 超时）；直连 Hub 时构建加: --build-arg PY_IMAGE=python:3.11-slim
# pip>=26.1.2（CVE-2026-8643）要求 Python >=3.10，勿再用 3.9-slim
ARG PY_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
FROM ${PY_IMAGE}
ARG PIP_INDEX=https://pypi.org/simple
ARG PIP_EXTRA_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY requirements.txt .
# pip 与依赖均优先 PyPI（新包同步快）；国内构建可保留 EXTRA_INDEX 加速
RUN pip install --no-cache-dir -U "pip>=26.1.2" -i "${PIP_INDEX}" \
    && pip install --no-cache-dir -r requirements.txt \
        -i "${PIP_INDEX}" --extra-index-url "${PIP_EXTRA_INDEX}"
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://dl.k8s.io/release/v1.29.14/bin/linux/${ARCH}/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
COPY . .
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["sh", "-c", "python init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8001"]

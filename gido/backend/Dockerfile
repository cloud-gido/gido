# 默认可用 DaoCloud 同步层（国内常见 hub 超时）；直连 Hub 时构建加: --build-arg PY_IMAGE=python:3.9-slim
ARG PY_IMAGE=docker.m.daocloud.io/library/python:3.9-slim
FROM ${PY_IMAGE}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
COPY . .
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["sh", "-c", "python init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8001"]

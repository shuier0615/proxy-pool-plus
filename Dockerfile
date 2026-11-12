FROM python:3.13-alpine

LABEL maintainer="jhao104 <j_hao104@163.com>"

WORKDIR /app

# 时区 + init 进程（tini 负责转发信号，保证容器优雅退出）
RUN apk add --no-cache tzdata tini bash && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone

COPY ./requirements.txt .

RUN apk add --no-cache --virtual .build-deps gcc musl-dev libxml2-dev libxslt-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del .build-deps

COPY . .

# 非 root 运行：API 与调度器只需写 /app（日志、PID 文件）
RUN addgroup -S app && adduser -S -G app app && chown -R app:app /app
USER app

EXPOSE 5010

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5010/health', timeout=3).status==200 else 1)"

ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]

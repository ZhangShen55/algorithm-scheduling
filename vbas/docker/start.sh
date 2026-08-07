#!/bin/bash

set -euo pipefail
export CONFIG_PATH="${CONFIG_PATH:-/workspace/config.toml}"
[[ -f "$CONFIG_PATH" ]] || { echo "[ERROR] 配置文件不存在"; exit 1; }
echo "[INFO] 配置文件存在，准备启动服务"

toml_value() {
    local key="$1"
    local default="$2"
    python - "$CONFIG_PATH" "$key" "$default" <<'PY'
import sys

path, key, default = sys.argv[1:4]
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

with open(path, "rb") as f:
    data = tomllib.load(f)

value = data.get(key, default)
print(value)
PY
}

INSTANCE_COUNT=$(toml_value INSTANCE_COUNT 1)
WORKERS_PER_INSTANCE=$(toml_value WORKERS_PER_INSTANCE 1)

echo "[INFO] 实例数量: $INSTANCE_COUNT, 每实例工作进程: $WORKERS_PER_INSTANCE"

if [ "$INSTANCE_COUNT" -gt 1 ]; then
    echo "[INFO] 启动 $INSTANCE_COUNT 个实例，每个 $WORKERS_PER_INSTANCE 个worker"

    # 生成端口列表（从8981开始）
    PORTS=()
    base_port=8981
    for ((i=0; i<INSTANCE_COUNT; i++)); do
        port=$((base_port + i))
        PORTS+=($port)
    done

    echo "[INFO] 将启动实例在端口: ${PORTS[*]}"

    # 生成Nginx配置
    generate_nginx_config() {
        local config_file="/etc/nginx/sites-enabled/tias_backend.conf"
        mkdir -p /etc/nginx/sites-enabled
        cat > "$config_file" << EOF
upstream tias_backend {
    least_conn;
    keepalive 32;
    keepalive_requests 1000;
    keepalive_timeout 60s;
$(printf '    server 127.0.0.1:%s;\n' "${PORTS[@]}")
}

server {
    listen 8881;
    server_name localhost;

    # 增加请求体大小限制
    client_max_body_size 100M;
    client_body_timeout 120s;
    client_header_timeout 120s;

    gzip on;
    gzip_types application/json text/plain;
    gzip_min_length 1000;

    location / {
        proxy_pass http://tias_backend;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;

        proxy_connect_timeout 30s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;

        # 禁用缓冲以减少延迟
        proxy_buffering off;
        proxy_request_buffering off;

    }

    location /nginx_status {
        stub_status on;
        access_log off;
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;
    }
}
EOF
    }

    # 启动函数
    start_uvicorn_instance() {
        local port=$1
        echo "[INFO] 启动uvicorn实例在端口 $port"

        uvicorn app.main:app \
                --host 0.0.0.0 \
                --port $port \
                --loop uvloop \
                --workers $WORKERS_PER_INSTANCE &

        local pid=$!
        echo $pid > "/tmp/tias-instance-$port.pid"

        # 等待并验证启动
        sleep 5
        if kill -0 $pid 2>/dev/null; then
            echo "[INFO] 实例 $port 启动成功，PID: $pid"
        else
            echo "[ERROR] 实例 $port 启动失败"
            return 1
        fi
    }

    cleanup() {
        # 直接输出到文件描述符，绕过缓冲
        printf "[INFO] 收到停止信号，正在关闭服务...\n" >&2

        # 停止nginx
        if [ ! -z "${NGINX_PID:-}" ] && kill -0 $NGINX_PID 2>/dev/null; then
            printf "[INFO] 停止Nginx (PID: $NGINX_PID)\n" >&2
            kill -TERM $NGINX_PID
            wait $NGINX_PID 2>/dev/null || true
            printf "[INFO] Nginx 已停止\n" >&2
        fi

        # 停止所有uvicorn实例 - 使用优雅关闭
        for port in "${PORTS[@]}"; do
            pid_file="/tmp/tias-instance-$port.pid"
            if [[ -f "$pid_file" ]]; then
                pid=$(cat "$pid_file")
                if kill -0 $pid 2>/dev/null; then
                    printf "[INFO] 停止实例 $port (PID: $pid)\n" >&2

                    # 发送SIGTERM给uvicorn主进程
                    kill -TERM $pid

                    # 等待更长时间让uvicorn完成关闭流程
                    printf "[INFO] 等待实例 $port 完成关闭...\n" >&2
                    for i in {1..60}; do
                        if ! kill -0 $pid 2>/dev/null; then
                            printf "[INFO] 实例 $port 已正常停止\n" >&2
                            break
                        fi
                        sleep 1
                    done

                    # 如果还没停止，强制杀死
                    if kill -0 $pid 2>/dev/null; then
                        printf "[WARN] 强制停止实例 $port\n" >&2
                        kill -KILL $pid 2>/dev/null || true
                    fi
                fi
                rm -f "$pid_file"
            fi
        done

        printf "[INFO] 所有服务已停止\n" >&2

        # 强制刷新输出
        sync
        sleep 1
        exit 0
    }
    # 注册信号处理
    trap cleanup SIGTERM SIGINT

    # 生成Nginx配置
    generate_nginx_config

    # 启动nginx
    echo "[INFO] 启动 Nginx..."
    nginx -t
    if [ $? -eq 0 ]; then
        nginx -g "daemon off;" &
        NGINX_PID=$!
        echo "[INFO] Nginx 启动完成，PID: $NGINX_PID"
    else
        echo "[ERROR] Nginx 配置测试失败"
        cleanup
        exit 1
    fi
    # 等待nginx启动
    sleep 3

    # 启动多个实例
    echo "[INFO] 启动 $INSTANCE_COUNT 个 uvicorn 实例..."
    for port in "${PORTS[@]}"; do
        start_uvicorn_instance $port
        sleep 2  # 避免同时启动造成资源竞争
    done

    echo "[INFO] 服务启动完成！访问地址: http://localhost:8881"

    # 监控循环
    while true; do
        # 检查 Nginx
        if ! kill -0 $NGINX_PID 2>/dev/null; then
            echo "[ERROR] Nginx 异常退出"
            cleanup
        fi
        # 检查实例
        for port in "${PORTS[@]}"; do
            pid_file="/tmp/tias-instance-$port.pid"
            if [[ -f "$pid_file" ]] && ! kill -0 $(cat "$pid_file") 2>/dev/null; then
                echo "[WARN] 重启实例 $port"
                start_uvicorn_instance $port
            fi
        done
        sleep 10
    done
else
    echo "[INFO] 启动单一uvicorn实例在端口8881, workers: $WORKERS_PER_INSTANCE"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8881 --loop uvloop --workers "$WORKERS_PER_INSTANCE"
fi

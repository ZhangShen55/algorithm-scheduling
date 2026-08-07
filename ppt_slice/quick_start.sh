#!/bin/bash
# 快速启动脚本

echo "=========================================="
echo "  Video PPT Slice Service - 快速启动"
echo "=========================================="
echo ""

# 检查 conda 环境
if ! conda env list | grep -q "ppt_slice"; then
    echo "❌ Conda 环境 'ppt_slice' 不存在"
    echo "请先运行: conda create -n ppt_slice python=3.9 -y"
    exit 1
fi

echo "✅ Conda 环境检查通过"

# 激活环境
source /root/anaconda3/etc/profile.d/conda.sh
conda activate ppt_slice

# 检查依赖
echo "检查依赖..."
if ! python -c "import fastapi, uvicorn, cv2, av" 2>/dev/null; then
    echo "❌ 依赖缺失，正在安装..."
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pip install av -i https://pypi.tuna.tsinghua.edu.cn/simple --prefer-binary
fi

echo "✅ 依赖检查通过"
echo ""

# 创建必要的目录
mkdir -p logs shared_results

echo "=========================================="
echo "  启动服务..."
echo "=========================================="
echo ""
echo "📍 访问地址:"
echo "   - API文档: http://localhost:9001/docs"
echo "   - ReDoc: http://localhost:9001/redoc"
echo "   - 健康检查: http://localhost:9001/health"
echo ""
echo "📝 日志文件:"
echo "   - 所有日志: logs/app.log"
echo "   - 错误日志: logs/error.log"
echo ""
echo "⚙️  配置:"
echo "   - 最大并发任务: 15"
echo "   - 帧队列缓冲: 25"
echo "   - 监听端口: 9001"
echo ""
echo "按 Ctrl+C 停止服务"
echo "=========================================="
echo ""

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 9001 --reload

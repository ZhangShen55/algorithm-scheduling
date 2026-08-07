# Docker生产部署

## 1. 生成加密模型

```bash
conda run --no-capture-output -n screen_det python docker/protect_models.py \
  --generate-key \
  --key-file docker/models-encrypted/model.key
```

生成的`screen.pt.enc`、`occlusion.pt.enc`和`model.key`不会提交Git，也不会进入Docker构建上下文。

## 2. 准备生产配置

生产`config.toml`至少需要：

```toml
[yolo]
device = "cuda:0"

[model_protection]
enabled = true
encrypted_model_root = "/run/screen-det/models-encrypted"
key_file = "/run/screen-det/models-encrypted/model.key"
decrypted_temp_root = "/dev/shm/screen-det-models"
cleanup_after_load = true
```

配置CUDA但容器内CUDA不可用时，服务启动失败，不回退CPU。

## 3. 构建镜像

构建上下文必须是项目根目录：

```bash
docker build -f docker/Dockerfile \
  -t screen_det:protected .
```

最终镜像使用Cython编译业务模块，不包含明文模型、加密模型、密钥、requirements、Dockerfile和构建脚本。

## 4. 启动容器

```bash
docker run -d \
  --name screen-det \
  --gpus all \
  -p 8880:8880 \
  -v "$PWD/config.toml:/app/config.toml:ro" \
  -v "$PWD/docker/models-encrypted:/run/screen-det/models-encrypted:ro" \
  screen_det:tag
```

`/health`只有在`screen.pt`和`occlusion.pt`都加载并预热后才返回ready。ready后可以删除宿主机`docker/models-encrypted/`中的密文和密钥，当前容器继续使用内存模型推理。

删除部署材料后，容器不能重启、扩容或重新加载模型；执行这些操作前必须重新生成并挂载完整材料。

## 5. 部署验收

```bash
conda activate screen_det
bash docker/run_deploy_verify.sh
```

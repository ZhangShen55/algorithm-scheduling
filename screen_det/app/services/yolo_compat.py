from __future__ import annotations

import types


def _legacy_aattn_forward(self, x):
    """旧版 YOLO12 权重使用 qk + v，而非合并的 qkv。"""
    B, _, H, W = x.shape
    N = H * W
    all_head_dim = getattr(self, "all_head_dim", self.head_dim * self.num_heads)

    qk = self.qk(x).flatten(2).transpose(1, 2)
    v = self.v(x).flatten(2).transpose(1, 2)
    if self.area > 1:
        qk = qk.reshape(B * self.area, N // self.area, -1)
        v = v.reshape(B * self.area, N // self.area, -1)
        B, N, _ = qk.shape

    q, k = qk.view(B, N, self.num_heads, self.head_dim * 2).split(
        [self.head_dim, self.head_dim], dim=-1
    )
    q = q.permute(0, 2, 3, 1)
    k = k.permute(0, 2, 3, 1)
    v = v.view(B, N, self.num_heads, self.head_dim).permute(0, 2, 3, 1)

    attn = (q.transpose(-2, -1) @ k) * (self.head_dim**-0.5)
    attn = attn.softmax(dim=-1)
    out = v @ attn.transpose(-2, -1)
    out = out.permute(0, 3, 1, 2)
    v = v.permute(0, 3, 1, 2)

    if self.area > 1:
        out = out.reshape(B // self.area, N * self.area, all_head_dim)
        v = v.reshape(B // self.area, N * self.area, all_head_dim)
        B, N, _ = out.shape

    out = out.reshape(B, H, W, all_head_dim).permute(0, 3, 1, 2).contiguous()
    v = v.reshape(B, H, W, all_head_dim).permute(0, 3, 1, 2).contiguous()
    return self.proj(out + self.pe(v))


def patch_legacy_aattn(yolo_model) -> int:
    """为缺少 qkv 的旧 AAttn 模块替换 forward，返回修补数量。"""
    net = yolo_model.model
    patched = 0
    for module in net.modules():
        if module.__class__.__name__ != "AAttn":
            continue
        if hasattr(module, "qkv"):
            continue
        if not (hasattr(module, "qk") and hasattr(module, "v")):
            continue
        if not hasattr(module, "all_head_dim"):
            module.all_head_dim = module.head_dim * module.num_heads
        module.forward = types.MethodType(_legacy_aattn_forward, module)
        patched += 1
    return patched

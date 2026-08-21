BEGIN;

COMMENT ON TABLE node_results IS
    '节点结果表，与任务节点一对零或一；当前结构化结果用于 OCR、ASR 与视觉分析，历史记录可能包含已退役的关键词或课程脑图结果';
COMMENT ON COLUMN node_results.result IS
    '当前保存 OCR、ASR 或视觉分析结构化 JSON 结果；历史行可能保留已退役的关键词或课程脑图结果';

COMMENT ON TABLE node_work_items IS
    '节点动态子项表；当前用于按 ppt_image_id 保存单张切片 OCR 状态，历史记录可能包含已退役的关键词子项';
COMMENT ON COLUMN node_work_items.result IS
    '当前保存单张 PPT 图片的 OCR 结构化 JSON 结果；历史行可能保留已退役的关键词结果';

COMMENT ON TABLE operator_instances IS
    '算子实例持久化审计快照表；Redis 是当前实时路由权威，历史行允许保留已经退役的算子事实';
COMMENT ON COLUMN operator_instances.operator_code IS
    '当前平台注册算子代码；text_analysis 仅作为退役前历史审计值保留，不再用于注册或路由';

COMMIT;

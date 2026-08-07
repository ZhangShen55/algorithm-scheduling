# == 你的 models.py 原样迁移（仅导入路径在其它文件里做了适配） ==
import time
import random
import json
import string
from pydantic import BaseModel, Field, RootModel, create_model
from typing import List, Optional, Literal, Union, TypeVar, Generic, Dict, Any
from app.services.llm_client import get_model_name

model_name = get_model_name()


def generate_id(prefix: str, k=29) -> str:
    suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=k))
    return f"{prefix}{suffix}"


class DocumentSkim(BaseModel):
    time: str
    content: str


class MindMapNode(BaseModel):
    id: str
    label: str
    time: str
    children: Optional[List['MindMapNode']] = None


class MindMap(BaseModel):
    nodes: List[MindMapNode]


class MindMapWrapper:
    def wrap_mindmap(self, mindmap_json_str):
        mindmap_data = json.loads(mindmap_json_str)

        def process_node(node):
            if 'children' in node:
                node['children'] = [self.process_child(child) for child in node['children']]
            else:
                node['children'] = None
            return MindMapNode(**node)

        def process_nodes(nodes):
            return [process_node(node) for node in nodes]

        processed_nodes = process_nodes(mindmap_data['mindmap']['nodes'])
        mindmap = MindMap(nodes=processed_nodes)
        return mindmap

    def process_child(self, child):
        if 'children' in child:
            child['children'] = [self.process_child(sub_child) for sub_child in child['children']]
        else:
            child['children'] = None
        return MindMapNode(**child)


class Summary(BaseModel):
    full_overview: str
    key_points: List[str]
    document_skims: List[DocumentSkim]


class ClassroomResultObject(BaseModel):
    summary: Summary
    mindmap: MindMap


class ModelCard(BaseModel):
    id: str = ""
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "owner"
    root: Optional[str] = None
    parent: Optional[str] = None
    permission: Optional[list] = None


class ModelList(BaseModel):
    object: str = "list"
    data: List[ModelCard] = ["glm-4"]


class FunctionCall(BaseModel):
    name: Optional[str] = None
    arguments: Optional[str] = None


class ChoiceDeltaToolCallFunction(BaseModel):
    name: Optional[str] = None
    arguments: Optional[str] = None


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0
    completion_tokens: Optional[int] = 0


class ChatCompletionMessageToolCall(BaseModel):
    index: Optional[int] = 0
    id: Optional[str] = None
    function: FunctionCall
    type: Optional[Literal["function"]] = 'function'


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: Optional[str] = None
    def to_dict(self):
        return self.dict()


class DeltaMessage(BaseModel):
    role: Optional[Literal["user", "assistant", "system"]] = None
    content: Optional[str] = None
    function_call: Optional[ChoiceDeltaToolCallFunction] = None
    tool_calls: Optional[List[ChatCompletionMessageToolCall]] = None


class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls"]


class ChatCompletionResponseStreamChoice(BaseModel):
    delta: DeltaMessage
    finish_reason: Optional[Literal["stop", "length", "tool_calls"]]
    index: int


class ChatCompletionResponse(BaseModel):
    model: str
    id: Optional[str] = Field(default_factory=lambda: generate_id('chatcmpl-', 29))
    object: Literal["chat.completion", "chat.completion.chunk"]
    choices: List[Union[ChatCompletionResponseChoice, ChatCompletionResponseStreamChoice]]
    created: Optional[int] = Field(default_factory=lambda: int(time.time()))
    system_fingerprint: Optional[str] = Field(default_factory=lambda: generate_id('fp_', 9))
    usage: Optional[UsageInfo] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.8
    top_p: Optional[float] = 0.8
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[Union[dict, List[dict]]] = None
    tool_choice: Optional[Union[str, dict]] = None
    repetition_penalty: Optional[float] = 1.1


T = TypeVar('T')

class GenericResponse(BaseModel, Generic[T]):
    model: str
    id: Optional[str] = Field(default_factory=lambda: generate_id('chatcmpl-', 29))
    result: Union[T,str]
    usage: Optional[UsageInfo] = None


# =============关键字===============
class TextContentByKeywordsObject(BaseModel):
    text: str
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称")
    max_tokens: Optional[int] = Field(default=4096, description="生成文本的最大token数，如果没有指定则默认为4096")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=None, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    frequency_penalty: float = Field(default=0.0, description="生成文本时选择词的频率惩罚参数(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    top_k: int = Field(default=50, description="生成文本时选择词的数量")
    temperature: float = Field(default=0.2, description="生成文本的随机性参数")
    tools: Optional[Union[dict, List[dict]]] = Field(default=None, description="工具列表，用于执行特定的函数调用")
    tool_choice: Optional[Union[dict, List[dict]]] = Field(default=None, description="工具选择，指定如何选择使用哪个工具")


class ExtractKeywordsCompletionResponseChoice(BaseModel):
    keywords: List[str] = []
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]


class Keyword(BaseModel):
    keyword : str
    times : list[str]

class ExtractKeywordsCompletionResponseChoice2(BaseModel):
    keywords: List[Keyword] = []
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]


class TextGenerationCompletionResponseChoice(BaseModel):
    text: str
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]



class HaiZhouCompletionResponseStreamChoice(BaseModel):
    index: int
    delta: DeltaMessage
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    finished_reason: Optional[Literal["stop", "too_long"]]


class GenericKeywordsResponse(
    GenericResponse[Union[ExtractKeywordsCompletionResponseChoice, HaiZhouCompletionResponseStreamChoice]]):
    pass


# =================文本摘要===================
class TextContentBySummaryObject(BaseModel):
    text: str
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称")
    max_tokens: Optional[int] = Field(default=1024, description="生成文本的最大token数，如果没有指定则默认为1024")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=1.0, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    temperature: float = Field(default=0.2, description="生成文本的随机性参数")
    tools: Optional[Union[dict, List[dict]]] = Field(default=None, description="工具列表，用于执行特定的函数调用")
    tool_choice: Optional[Union[dict, List[dict]]] = Field(default=None,
                                                           description="工具选择，指定如何选择使用哪个工具")


class ExtractSummaryCompletionResponseChoice(BaseModel):
    summary: str
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]


class GenericSummaryResponse(
    GenericResponse[Union[ExtractSummaryCompletionResponseChoice, HaiZhouCompletionResponseStreamChoice]]):
    pass


class ClassroomSummaryRequestObject(BaseModel):
    text: str
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称，默认为deepseek-r1:32b")
    max_tokens: Optional[int] = Field(default=1024, description="生成文本的最大token数，如果没有指定则默认为1024")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=1.0, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    temperature: float = Field(default=0, description="生成文本的随机性参数")
    tools: Optional[Union[dict, List[dict]]] = Field(default=None, description="工具列表，用于执行特定的函数调用")
    tool_choice: Optional[Union[dict, List[dict]]] = Field(default=None,
                                                           description="工具选择，指定如何选择使用哪个工具")


class ContentRequestObject(BaseModel):
    text: Union[str, List[str]] = Field(description="字符串集合，既可为单个字符串也可为字符串列表")
    language: Union[List[str]] = Field(default=["zh", "en"], description="目标语言")
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称，默认为deepseek-r1:32b")
    max_tokens: Optional[int] = Field(default=8192, description="生成文本的最大token数，如果没有指定则默认为8192")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=None, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    frequency_penalty: float = Field(default=0.0, description="生成文本时选择词的频率惩罚参数(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    top_k: int = Field(default=50, description="生成文本时选择词的数量")
    temperature: float = Field(default=0.6, description="生成文本的随机性参数")
    tools: Optional[Union[dict, List[dict]]] = Field(default=None, description="工具列表，用于执行特定的函数调用")
    tool_choice: Optional[Union[dict, List[dict]]] = Field(default=None,
                                                           description="工具选择，指定如何选择使用哪个工具")



class TextGenerationRequestObject(BaseModel):
    courses: Union[List[str]] = Field(description="课程名集合，既可为单个字符串也可为字符串列表")
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称，默认为deepseek-r1:32b")
    max_tokens: Optional[int] = Field(default=8192, description="生成文本的最大token数，如果没有指定则默认为8192")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=None, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    frequency_penalty: float = Field(default=0.0, description="生成文本时选择词的频率惩罚参数(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    top_k: int = Field(default=50, description="生成文本时选择词的数量")
    temperature: float = Field(default=0.6, description="生成文本的随机性参数")
    tools: Optional[Union[dict, List[dict]]] = Field(default=None, description="工具列表，用于执行特定的函数调用")
    tool_choice: Optional[Union[dict, List[dict]]] = Field(default=None,
                                                           description="工具选择，指定如何选择使用哪个工具")



class TranslateContent(BaseModel):
    content: List[str]
    language: str


class TranslateContentObject(BaseModel):
    sentences: List[dict]
    language: List[str]
    inputSize: int


class ContentItem(BaseModel):
    index: int
    translate_result: str


class TranslateItem(BaseModel):
    content: List[str]
    language: str


class TranslateCompletionResponseChoice(BaseModel):
    contents: List[TranslateItem]
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]


class GenericTranslateResponse(
    GenericResponse[Union[TranslateCompletionResponseChoice, HaiZhouCompletionResponseStreamChoice]]):
    pass


class UserInputObject(BaseModel):
    sentences: List[str]
    languages: List[str]
    input_size: int


class PyloadModel(BaseModel):
    model: str = None
    messages: List[ChatMessage] = None
    stream: bool = False
    max_tokens: int = None
    temperature: float = None
    top_p: float = None
    top_k: int = None
    frequency_penalty: float = None
    repetition_penalty: float = None

    # 构建请求体
    @classmethod
    def build_request_object(self, request: (ContentRequestObject)):
        messages = [msg.to_dict() for msg in request.messages]
        return {
            "model": request.model,
            "messages": messages,
            "stream": request.stream,
            "options": {
                "temperature": request.temperature,
                "top_p": request.top_p,
                "top_k": request.top_k,
                "repetition_penalty": request.repetition_penalty,
                "num_predict": request.max_tokens,
                "frequency_penalty": request.frequency_penalty
            }

        }


class TextSegment(BaseModel):
    text: str
    bg: float
    ed: float


class QuestionSegment(BaseModel):
    segment_text: str = Field(description="转写片段文本")
    bg: float = Field(description="片段开始时间，单位秒")
    ed: float = Field(description="片段结束时间，单位秒")
    role: Optional[str] = Field(default=None, description="兼容旧字段，新流程不依赖")


class QuestionClassificationRequestObject(BaseModel):
    segments: List[QuestionSegment] = Field(default_factory=list, description="问句分类转写片段")
    min_len: int = Field(default=1, description="重建问句最小有效字符数")
    confidence: Optional[float] = Field(default=None, description="兼容旧字段，不作为硬阈值")
    task_id: Optional[str] = Field(default=None, description="兼容任务 ID")
    course_id: Optional[str] = Field(default=None, description="兼容课程 ID")
    model: Optional[str] = Field(default=None, description="模型名称，不传则使用 config.toml 默认模型")


class TimeRange(BaseModel):
    start: float
    end: float


class TextSegmentsObject(BaseModel):
    textSegments: List[TextSegment]


class LanguageExpressionAnalysisRequestObject(BaseModel):
    textSegments: List[TextSegment] = Field(description="文本分段列表")
    course_start: Optional[float] = Field(default=None, description="课程开始时间，单位秒")
    course_end: Optional[float] = Field(default=None, description="课程结束时间，单位秒")
    breaks: List[TimeRange] = Field(default_factory=list, description="课间时间段列表")
    model: Optional[str] = Field(default=None, description="模型名称，不传则使用 config.toml 默认模型")
    temperature: Optional[float] = Field(default=None, description="生成温度，不传则使用语言表达分析默认温度")


class CourseKnowledgeCorpusAnalysisRequestObject(BaseModel):
    textSegments: List[TextSegment] = Field(default_factory=list, description="文本分段列表")
    course_start: float = Field(description="课程开始时间，单位秒")
    course_end: float = Field(description="课程结束时间，单位秒")
    breaks: List[TimeRange] = Field(default_factory=list, description="课间时间段列表")
    model: Optional[str] = Field(default=None, description="模型名称，不传则使用 config.toml 默认模型")
    max_knowledge_points: Optional[int] = Field(default=None, description="最多返回知识点数量")
    max_corpus: Optional[int] = Field(default=None, description="最多返回语料数量")


class StudentInteractionAnalysisRequestObject(BaseModel):
    textSegments: List[TextSegment] = Field(default_factory=list, description="文本分段列表")
    course_start: float = Field(description="课程开始时间，单位秒")
    course_end: float = Field(description="课程结束时间，单位秒")
    breaks: List[TimeRange] = Field(default_factory=list, description="课间时间段列表")
    model: Optional[str] = Field(default=None, description="模型名称，不传则使用 config.toml 默认模型")


class Summary(BaseModel):
    full_overview: str  # 全文概述
    key_points: List[str]  # 关键要点
    document_skims: List[dict]  # 文档速读内容片段


class MindmapNode(BaseModel):
    id: str
    label: str
    time: str
    children: List[dict]


class Mindmap(BaseModel):
    nodes: List[MindmapNode]


class ClassroomOverviewRequestObject(BaseModel):
    textSegments: list[TextSegment] = Field(description="文本分段列表")
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称")
    max_tokens: Optional[int] = Field(default=16384, description="生成文本的最大token数，如果没有指定则默认为8192")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=None, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    frequency_penalty: float = Field(default=0.0, description="生成文本时选择词的频率惩罚参数(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    top_k: int = Field(default=50, description="生成文本时选择词的数量")
    temperature: float = Field(default=0.7, description="生成文本的随机性参数")


class CourseAnalysisRequestObject(BaseModel):
    textSegments: list[TextSegment] = Field(description="文本分段列表")
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称")
    max_tokens: Optional[int] = Field(default=16384, description="生成文本的最大token数，如果没有指定则默认为8192")
    stream: bool = Field(default=False, description="是否以流式方式返回结果")
    repetition_penalty: float = Field(default=None, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    frequency_penalty: float = Field(default=0.0, description="生成文本时选择词的频率惩罚参数(0,2]")
    top_p: float = Field(default=1.0, description="生成文本时选择词的概率阈值(0,1]")
    top_k: int = Field(default=50, description="生成文本时选择词的数量")
    temperature: float = Field(default=0.6, description="生成文本的随机性参数")


class Mindmap(BaseModel):
    overall_label: str  # 总体标签
    total_time: str  # 总时间区间
    nodes: List[MindmapNode]


class MindmapNode(BaseModel):
    id: str
    label: str
    time: str
    children: Optional[List[MindmapNode]] = None


# 处理递归模型时需要更新引用
MindmapNode.update_forward_refs()


class DocumentSkim(BaseModel):
    time: str
    overview : str  # 速读概览(新增)
    content: str


class CourseOverviewResult(BaseModel):
    full_overview: str
    key_points: List[str]
    document_skims: List[DocumentSkim]
    mindmap: Mindmap


# 课堂概览 结果填充
class CourseOverviewCompletionResponseChoice(BaseModel):
    overview: CourseOverviewResult
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]


# =========== 课堂知识点覆盖 请求 相应定义===============

class ExtractKeywordsCoverRequestObject(BaseModel):
    text:str = Field(description="文本内容")
    course_name:str = Field(description="课程名称")
    course_id:str = Field(default=None, description="课程id")
    task_id:str = Field(default=None, description="任务id")
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称")
    temperature: float = Field(default=0.6, description="生成文本的随机性参数")

# =========== 课堂知识点覆盖 请求 相应定义===============
class CourseEvaluationRequestObject(BaseModel):
    text:str = Field(description="转写文本内容")
    course_name:str = Field(description="课程名称")
    course_model:str = Field(description="课程模型")
    course_knowledge:list = Field(default=[],description="课程知识点")
    blackboard_times:int = Field(default=0,description="板书次数")
    question_times:int = Field(default=0,description="提问次数")
    interaction_times:int = Field(default=0,description="互动次数")
    # 参与问答的学生人数
    question_stu_times:int = Field(default=0,description="参与问答的学生人数")
    speak_stu_time:float = Field(default=0,description="学生参与发言的总时间秒")
    course_distribution:list = Field(default=[0.8,0.1,0.1],description="课堂时间分布")
    standup_times:int = Field(default=0,description="学生站立次数")
    raisehead_times:int = Field(default=0,description="学生抬头次数")
    raisehead_rate:float = Field(default=0,description="抬头率")
    concentration_rate:float = Field(default=0,description="专注率")
    # 学生互动率：（小数）
    student_interaction_rate:float = Field(default=0.6,description="学生互动率")
    # 学生的迟到率：（小数）
    late_rate:float = Field(default=0,description="学生的迟到率")
    # 学生的早退率：（小数）
    leave_early_rate:float = Field(default=0,description="学生的早退率")
    # 学生出勤率：（小数）
    attendance_rate:float = Field(default=1.0,description="学生出勤率")
    # 学生前排入座率：（小数）
    frontrow_rate:float = Field(default=0.8,description="学生前排入座率")
    # 老师的普通话水平：（优秀、良好、一般）
    mandarin_level:str = Field(default="良好",description="老师的普通话水平")
    # 老师课堂站立讲台时长：（分钟）
    teacher_standup_time:int = Field(default=45,description="老师课堂站立讲台时长")
    # 巡视时长：（分钟）
    patrol_time:int = Field(default=5,description="巡视时长")
    # 巡视次数：（整数）
    patrol_times:int = Field(default=0,description="巡视次数")
    messages: List[ChatMessage] = Field(default=None, description="构建消息列表")
    model: str = Field(default=model_name, description="模型名称")
    temperature: float = Field(default=0.6, description="生成文本的随机性参数")

# =========== AI写评论 请求 相应定义===============
from pydantic import Field, field_validator

class AiGeneratedReviewRequestObject(BaseModel):
    advantage_tags: List[str] = Field(default_factory=list, description="优势标签：不固定数量")
    problem_tags: List[str] = Field(default_factory=list, description="问题标签：不固定数量")
    max_chars: int = Field(..., description="生成字数上限（100–800）")
    model: Optional[str] = Field(default=None, description="可指定大模型名；为空则用后端默认")
    temperature: Optional[float] = Field(default=0.4, description="可选温度")

    @field_validator("advantage_tags", "problem_tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        seen, cleaned = set(), []
        for s in v:
            t = (s or "").strip()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t)
        return cleaned
    
    @field_validator("max_chars")
    @classmethod
    def _check_max_chars(cls, v: int) -> int:
        # 允许字符串数字自动转为 int（如前端表单传 "300"）
        if isinstance(v, str) and v.isdigit():
            v = int(v)
        if not isinstance(v, int):
            raise ValueError("max_chars 必须为整数")
        if v < 100 or v > 800:
            raise ValueError("max_chars 必须在 100 到 800 之间")
        return v
    

# AI文本润色
class AiPolishRequestObject(BaseModel):
    text: str = Field(..., description = "待润色文本")
    min_chars: int = Field(default = 200, description = "润色后文本的最少字数")
    max_chars: int = Field(default = 800, description = "润色后文本的最多字数")
    model: Optional[str] = Field(default=None, description="可指定大模型名；为空则用后端默认")
    temperature: Optional[float] = Field(default=0.4, description="可选温度")

    @field_validator("text")
    @classmethod
    def _check_text(cls, v: str) -> str:
        t = (v or "").strip()
        if not t:
            raise ValueError("text 不能为空")
        return t
    
    @field_validator("min_chars", "max_chars")
    @classmethod
    def _check_chars(cls, v: int, info) -> int:
        # 允许字符串数字自动转为 int（如前端表单传 "300"）
        if isinstance(v, str) and v.isdigit():
            v = int(v)
        if not isinstance(v, int):
            raise ValueError(f"{info.field_name} 必须为整数")
        if v < 50 or v > 800:
            raise ValueError(f"{info.field_name} 必须在 50 到 800 之间")
        return v

# AI 文本总结
class AiSummaryItem(BaseModel):
    id: str = Field(..., description="业务ID")
    text: str = Field(..., description="原始文本")

class AiSummaryItemRequestObject(BaseModel):
    id: str = Field(..., description="业务ID")
    text: str = Field(..., description="原始文本")
    model: Optional[str] = Field(default=None, description="可指定大模型名；为空则用后端默认")
    temperature: Optional[float] = Field(default=0.4, description="可选温度")

class AiSummaryBatchRequestObject(BaseModel):
    items: List[AiSummaryItem]
    model: Optional[str] = Field(default=None, description="可指定大模型名；为空则用后端默认")
    temperature: Optional[float] = Field(default=0.4, description="可选温度")
    max_concurrency: Optional[int] = Field(default=6, description="批量并发上限（1–32）")

# == MT 请求 相应定义===============

languages_code_list = [
    "zh",
    "en",
    "fr",
    "pt",
    "es",
    "ja",
    "tr",
    "ru",
    "ar",
    "ko",
    "th",
    "it",
    "de",
    "vi",
    "ms",
    "id",
    "tl",
    "hi",
    "zh-Hant",
    "pl",
    "cs",
    "nl",
    "km",
    "my",
    "fa",
    "gu",
    "ur",
    "te",
    "mr",
    "he",
    "bn",
    "ta",
    "uk",
    "bo",
    "kk",
    "mn",
    "ug",
    "yue"
]

# 暂时用不着
languages_code_map = {
    "zh": "Chinese",               # 中文（简体）
    "en": "English",               # 英语
    "fr": "French",                # 法语
    "pt": "Portuguese",            # 葡萄牙语
    "es": "Spanish",               # 西班牙语
    "ja": "Japanese",              # 日语
    "tr": "Turkish",               # 土耳其语
    "ru": "Russian",               # 俄语
    "ar": "Arabic",                # 阿拉伯语
    "ko": "Korean",                # 韩语
    "th": "Thai",                  # 泰语
    "it": "Italian",               # 意大利语
    "de": "German",                # 德语
    "vi": "Vietnamese",            # 越南语
    "ms": "Malay",                 # 马来语
    "id": "Indonesian",            # 印尼语
    "tl": "Filipino",              # 菲律宾语
    "hi": "Hindi",                 # 印地语
    "zh-Hant": "繁体中文",  # 繁体中文
    "pl": "Polish",                # 波兰语
    "cs": "Czech",                 # 捷克语
    "nl": "Dutch",                 # 荷兰语
    "km": "Khmer",                 # 高棉语
    "my": "Burmese",               # 缅甸语
    "fa": "Persian",               # 波斯语
    "gu": "Gujarati",              # 古吉拉特语
    "ur": "Urdu",                  # 乌尔都语
    "te": "Telugu",                # 泰卢固语
    "mr": "Marathi",               # 马拉地语
    "he": "Hebrew",                # 希伯来语
    "bn": "Bengali",               # 孟加拉语
    "ta": "Tamil",                 # 泰米尔语
    "uk": "Ukrainian",             # 乌克兰语
    "bo": "Tibetan",               # 藏语
    "kk": "Kazakh",                # 哈萨克语
    "mn": "Mongolian",             # 蒙古语
    "ug": "Uyghur",                # 维吾尔语
    "yue": "粤语"             # 粤语
}
class TranslateRequestObject(BaseModel):
    text: Union[str, List[str]] = Field(description="字符串集合，既可为单个字符串也可为字符串列表")
    language: Union[List[str]] = Field(default=languages_code_list, description="目标语言缩写")
    segment_size: int = Field(default=None, description="默认为None,仅仅使用部署文件中配置,用于分割")
    # 下面参数可以释放给请求端
    model: str = Field(default="hy-mt", description="模型名称")
    # max_tokens: Optional[int] = Field(default=None, description="生成文本的最大token数，如果没有指定则默认为4096")
    # 官方推荐参数
    repetition_penalty: float = Field(default=1.05, description="重复惩罚参数，用于减少生成文本中的重复内容(0,2]")
    top_p: float = Field(default=0.6, description="生成文本时选择词的概率阈值(0,1]")
    top_k: int = Field(default=20, description="生成文本时选择词的数量(1,100]")
    temperature: float = Field(default=0.0, description="生成文本的随机性参数")

# == MT 响应 相应定义===============
T = TypeVar('T')

class GenericResponse(BaseModel, Generic[T]):
    model: str = Field(default="seacraft-mt", description="model name")
    id: Optional[str] = Field(default_factory=lambda: generate_id('seaCraft-', 29))
    result: T
    usage: Optional[UsageInfo] = None

class TranslateItem(BaseModel):
    content: List[str]
    language: str

class TranslateCompletionResponseChoice(BaseModel):
    contents: List[TranslateItem]
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()),description="处理完成时间")
    process_time_ms: Optional[int] = Field(default=None, description="处理时间，单位毫秒")
    finished_reason: Literal["finished", "too_long"] = Field(default="finished")

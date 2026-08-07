# app/person.py
from bson import ObjectId
from typing import List, Optional
from app.core.config import settings
from app.core.exceptions import DatabaseError

# 获取单个人物
async def get_person(db, person_id: str):
    return await db["persons"].find_one({"_id": ObjectId(person_id)}, {"embedding": 0})

# 获取所有人物
async def get_persons(db, skip: int = 0, limit: int = 100):
    return await db["persons"].find({}, {"embedding": 0, "tip": 0}).skip(skip).limit(limit).to_list(length=limit)


# 创建人物
async def create_person(db, person_dict: dict):

    # 唯一性校验
    exists = await db.persons.find_one(
        {"number": person_dict["number"]}, {"embedding": 0, "tip": 0}
    )

    if exists:
        # raise DatabaseError(status_code=400, detail=f"Person已存在请勿重复创建,name: {person_dict['name']}, number: {person_dict['number']}")
        raise DatabaseError(status_code=400, detail=f"该人物已存在请勿重复创建 预计存入number：{person_dict['number']}，实际库中存在 name: {exists['name']}, number: {exists['number']}")
    # 插入
    res = await db["persons"].insert_one(person_dict)
    return await db["persons"].find_one({"_id": res.inserted_id}, {"embedding": 0})

# 创建或更新人物（根据number判断）
async def update_or_create_person(db, person_dict: dict):
    """
    根据 number 判断：
    - 如果 number 已存在，则更新该人物的所有字段
    - 如果 number 不存在，则创建新人物

    返回: (doc, is_updated)
        - doc: 人物文档
        - is_updated: True表示更新，False表示新建
    """
    # 查找是否存在相同 number 的人物
    exists = await db.persons.find_one(
        {"number": person_dict["number"]}, {"embedding": 0}
    )

    if exists:
        # 存在则更新
        result = await db["persons"].update_one(
            {"number": person_dict["number"]},
            {"$set": person_dict}
        )
        # 返回更新后的文档
        updated_doc = await db["persons"].find_one({"number": person_dict["number"]}, {"embedding": 0})
        return updated_doc, True
    else:
        # 不存在则插入
        res = await db["persons"].insert_one(person_dict)
        new_doc = await db["persons"].find_one({"_id": res.inserted_id}, {"embedding": 0})
        return new_doc, False

# 删除人物
async def delete_person(db, person_id: str):
    result = await db["persons"].delete_one({"_id": ObjectId(person_id)})
    if result.deleted_count == 0:
        return None
    return {"_id": person_id}


async def delete_persons_by_name(db, name_keyword: str):
    """
    根据姓名模糊删除人物,返回删除数量和被删除的人物信息
    """
    # 先查询要删除的人物信息
    persons = await db["persons"].find(
        {"name": {"$regex": name_keyword, "$options": "i"}},
        {"_id": 1, "name": 1, "number": 1}
    ).to_list(length=None)

    if not persons:
        return 0, []

    # 执行删除
    result = await db["persons"].delete_many({"name": {"$regex": name_keyword, "$options": "i"}})

    # 返回删除数量和人物信息列表
    info_list = [{"id": str(p.get("_id")), "number": p.get("number"), "name": p.get("name")} for p in persons]
    return result.deleted_count, info_list

async def delete_person_by_number(db, number: str):
    """
    根据 number 精确删除人物,返回删除数量和被删除的人物信息
    """
    # 先查询要删除的人物信息
    person = await db["persons"].find_one(
        {"number": number},
        {"_id": 1, "name": 1, "number": 1}
    )

    if not person:
        return 0, []

    # 执行删除
    result = await db["persons"].delete_one({"number": number})

    if result.deleted_count == 0:
        return 0, []

    # 返回删除数量和人物信息列表(保持与 delete_persons_by_name 一致)
    info_list = [{"id": str(person.get("_id")), "number": person.get("number"), "name": person.get("name")}]
    return 1, info_list


async def delete_person_by_id(db, id: str):
    """
    根据ID删除人物,返回删除数量和被删除的人物信息
    """
    # 先查询要删除的人物信息
    person = await db["persons"].find_one(
        {"_id": ObjectId(id)},
        {"_id": 1, "name": 1, "number": 1}
    )

    if not person:
        return 0, []

    # 执行删除
    result = await db["persons"].delete_one({"_id": ObjectId(id)})

    if result.deleted_count == 0:
        return 0, []

    # 返回删除数量和人物信息列表(保持与其他删除方法一致)
    info_list = [{"id": str(person.get("_id")), "number": person.get("number"), "name": person.get("name")}]
    return 1, info_list


async def delete_persons_by_name_exact(db, name: str):
    """
    按人物姓名精确匹配删除
    """
    result = await db["persons"].delete_many({"name": name})
    return result.deleted_count


async def get_persons_by_name(db, name_keyword: str):
    """
    根据姓名模糊查询人物
    """
    persons = await db["persons"].find({"name": {"$regex": name_keyword, "$options": "i"}},{"embedding": 0}).to_list(length=100)
    if not persons:
        return None
    return persons


async def get_person_by_id(db, person_id: str):
    """
    根据ID精确查询人物
    """
    person = await db["persons"].find_one({"_id": ObjectId(person_id)}, {"embedding": 0})
    return person

async def get_person_by_number(db, number: str):
    """
    根据 number 精确查询人物
    :param db: Motor 数据库对象
    :param number: 人物编号（字符串）
    :return: 查到则返回 dict；未查到返回 None（或按需抛 404）
    """
    person = await db["persons"].find_one({"number": number}, {"embedding": 0})
    if not person:
        return None
    return person


async def get_persons_by_name_and_number(db, name: str, number: str):
    """
    根据 name + number 组合精确查询人物(1条数据)
    """
    person = await db["persons"].find_one(
        {"name": {"$regex": name, "$options": "i"}, "number": number}, {"embedding": 0}
    )
    return person

async def get_persons_list_dynamic(db, name: Optional[str], number: Optional[str], limit: int = 20):
    """
    动态构建查询条件并返回列表
    """
    query = {}
    
    # 动态组装查询条件
    if name:
        query["name"] = {"$regex": name, "$options": "i"} # 模糊且忽略大小写
    if number:
        # 精准
        query["number"] = number

    # 获取游标
    cursor = db["persons"].find(
        query, 
        {"embedding": 0}
    )
    
    return await cursor.limit(limit).to_list(length=limit)


LIMIT = settings.db.limit
async def get_embeddings_for_match(db, limit=LIMIT):
    cursor = db["persons"].find(
        {}, {"embedding": 1, "name": 1, "number": 1,"photo_path": 1}
    )
    return await cursor.limit(limit).to_list(length=limit)


async def get_targets_embeddings(db, targets: List[str]) -> List[dict]:
    """
    通过人员编号列表，获取对应的人脸 embedding

    Args:
        db: 数据库连接
        targets: 人员编号列表，如 ["001", "002", "003"]

    Returns:
        包含 embedding 的人员文档列表
    """
    if not targets:
        return []

    projection = {"embedding": 1, "name": 1, "number": 1, "photo_path": 1}
    cursor = db["persons"].find(
        {"number": {"$in": targets}},  # 使用 $in 操作符查询
        projection
    )

    docs = await cursor.to_list(length=None)

    # 可选：记录未找到的编号（用于调试）
    found_numbers = {doc.get("number") for doc in docs}
    missing = set(targets) - found_numbers
    if missing:
        from app.core.logger import get_logger
        logger = get_logger(__name__)
        logger.warning(f"[get_targets_embeddings] 以下编号未在数据库中找到: {missing}")

    return docs
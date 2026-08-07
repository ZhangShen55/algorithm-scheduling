from app.models.response.person_interface_rep import PersonRead

def doc_to_person_read(doc: object) -> PersonRead:
    return PersonRead(
        id=str(doc["_id"]),
        name=doc.get("name"),
        number=doc.get("number"),
        photo_path=doc.get("photo_path"),
        bbox=doc.get("bbox"),
    )

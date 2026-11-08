from service.repositories import magic_repository
from service.domain.models import Magics
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from typing import Dict


def get_all_magics():
    magics = magic_repository.get_all_magics()
    return [Magics(**magic) for magic in magics]


def get_magic_by_id(magic_id: int):
    magic = magic_repository.get_magic_by_id(magic_id)
    if not magic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Magic not found')
    return Magics(**magic) if magic else None


def create_magic(magic: Magics):
    check_if_exists(
        get_all=get_all_magics,
        attr_name="Name",
        attr_value=magic.Name,
        exception_detail='Name already exist'
    )
    magic_id = magic_repository.create_magic(magic)
    return get_magic_by_id(magic_id)


def update_magic(magic_id: int, magic: Dict):
    check_for_duplicates(
        get_all=get_all_magics,
        check_id=magic_id,
        attr_name="Name",
        attr_value=magic.get("name"),
        exception_detail='Name already exist'
    )
    magic_repository.update_magic(magic_id, magic)
    return {"message": "Magic updated successfully"}


def delete_magic(magic_id: int):
    get_magic_by_id(magic_id)
    magic_repository.delete_magic(magic_id)
    return {"message": "Magic deleted successfully"}

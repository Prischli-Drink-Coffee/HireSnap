from service.repositories import personality_repository
from service.domain.models import Personalities
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from typing import Dict


def get_all_personalities():
    personalities = personality_repository.get_all_personalities()
    return [Personalities(**personality) for personality in personalities]


def get_personality_by_id(personality_id: int):
    personality = personality_repository.get_personality_by_id(personality_id)
    if not personality:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Personality not found')
    return Personalities(**personality) if personality else None


def create_personality(personality: Personalities):
    check_if_exists(
        get_all=get_all_personalities,
        attr_name="Name",
        attr_value=personality.Name,
        exception_detail='Name already exist'
    )
    personality_id = personality_repository.create_personality(personality)
    return get_personality_by_id(personality_id)


def update_personality(personality_id: int, personality: Dict):
    check_for_duplicates(
        get_all=get_all_personalities,
        check_id=personality_id,
        attr_name="Name",
        attr_value=personality.get("name"),
        exception_detail='Name already exist'
    )
    personality_repository.update_personality(personality_id, personality)
    return {"message": "Personality updated successfully"}


def delete_personality(personality_id: int):
    get_personality_by_id(personality_id)
    personality_repository.delete_personality(personality_id)
    return {"message": "Personality deleted successfully"}

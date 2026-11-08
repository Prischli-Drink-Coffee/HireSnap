from service.repositories import personality_candidate_repository
from service.domain.models import PersonalityCandidates
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from service.services.personality_services import get_personality_by_id
from service.services.user_services import get_user_by_id
from typing import Dict


def get_all_personality_candidates():
    personality_candidates = personality_candidate_repository.get_all_personality_candidates()
    return [PersonalityCandidates(**personality_candidate) for personality_candidate in personality_candidates]


def get_personality_candidate_by_id(personality_candidate_id: int):
    personality_candidate = personality_candidate_repository.get_personality_candidate_by_id(personality_candidate_id)
    if not personality_candidate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Personality candidate not found')
    return PersonalityCandidates(**personality_candidate) if personality_candidate else None


def create_personality_candidate(personality_candidate: PersonalityCandidates):
    get_personality_by_id(personality_candidate.PersonalityID)
    get_user_by_id(personality_candidate.UserID)
    personality_candidate_id = personality_candidate_repository.create_personality_candidate(personality_candidate)
    return get_personality_candidate_by_id(personality_candidate_id)


def update_personality_candidate(personality_candidate_id: int, personality_candidate: Dict):
    get_personality_candidate_by_id(personality_candidate_id)
    if personality_candidate.get("personality_id"):
        get_personality_by_id(personality_candidate.get("personality_id"))
    if personality_candidate.get("user_id"):
        get_user_by_id(personality_candidate.get("user_id"))
    personality_candidate_repository.update_personality_candidate(personality_candidate_id, personality_candidate)
    return {"message": "Personality candidate updated successfully"}


def delete_personality_candidate(personality_candidate_id: int):
    get_personality_candidate_by_id(personality_candidate_id)
    personality_candidate_repository.delete_personality_candidate(personality_candidate_id)
    return {"message": "Personality candidate deleted successfully"}

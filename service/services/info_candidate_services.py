from service.repositories import info_candidate_repository
from service.domain.models import InfoCandidates
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from typing import Dict


def get_all_info_candidates():
    info_candidates = info_candidate_repository.get_all_info_candidates()
    return [InfoCandidates(**info_candidate) for info_candidate in info_candidates]


def get_info_candidate_by_id(info_candidate_id: int):
    info_candidate = info_candidate_repository.get_info_candidate_by_id(info_candidate_id)
    if not info_candidate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Info candidate not found')
    return InfoCandidates(**info_candidate) if info_candidate else None


def create_info_candidate(info_candidate: InfoCandidates):
    check_if_exists(
        get_all=get_all_info_candidates,
        attr_name="Name",
        attr_value=info_candidate.Name,
        exception_detail='Name already exist'
    )
    info_candidate_id = info_candidate_repository.create_info_candidate(info_candidate)
    return get_info_candidate_by_id(info_candidate_id)


def update_info_candidate(info_candidate_id: int, info_candidate: Dict):
    check_for_duplicates(
        get_all=get_all_info_candidates,
        check_id=info_candidate_id,
        attr_name="Name",
        attr_value=info_candidate.get("name"),
        exception_detail='Name already exist'
    )
    info_candidate_repository.update_info_candidate(info_candidate_id, info_candidate)
    return {"message": "Info candidate updated successfully"}


def delete_info_candidate(info_candidate_id: int):
    get_info_candidate_by_id(info_candidate_id)
    info_candidate_repository.delete_info_candidate(info_candidate_id)
    return {"message": "Info candidate deleted successfully"}

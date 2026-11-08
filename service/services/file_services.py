from service.repositories import file_repository
from service.domain.models import Files
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists


def get_all_files():
    files = file_repository.get_all_files()
    return [Files(**file) for file in files]


def get_file_by_id(file_id: int):
    file = file_repository.get_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='File not found')
    return Files(**file) if file else None


def create_file(file: Files):
    check_if_exists(
        get_all=get_all_files,
        attr_name="Url",
        attr_value=file.Url,
        exception_detail='File already exist'
    )
    file_id = file_repository.create_file(file)
    return get_file_by_id(file_id)


def update_file(file_id: int, file: Files):
    check_for_duplicates(
        get_all=get_all_files,
        check_id=file_id,
        attr_name="Url",
        attr_value=file.Url,
        exception_detail='File already exist'
    )
    file_repository.update_file(file_id, file)
    return {"message": "File updated successfully"}


def delete_file(file_id: int):
    get_file_by_id(file_id)
    file_repository.delete_file(file_id)
    return {"message": "File deleted successfully"}

import os
import uuid
import numpy as np
from service.services import (file_services, embedding_services, personality_services, candidate_vacancy_services, personality_vacancy_services,
                          personality_candidate_services, info_candidate_services, user_services, personality_services, vacancy_services)
from service.domain.models import Users, Embeddings, Files, InfoCandidates, Personalities, PersonalityCandidates, Users, Vacancies, PersonalityVacancies, CandidateVacancies
from fastapi import HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from service.infrastructure.uploads import write_file_into_server, write_embedding_into_server
from service.shared.urls import return_url_object
from fastapi.encoders import jsonable_encoder
from service.configuration.logging import setup_logging
from service.shared.debug import debug_ex, debug_err, debug_info
from sklearn.metrics.pairwise import cosine_distances
from sklearn.preprocessing import MinMaxScaler
# The embedding pipeline lives in the ml sidecar (ml/, docs/SIDECARS.md 4); this
# import is the TRANSPORT. It is the only way to reach inference from the backend:
# there is no in-process path any more, so this module no longer pulls torch into
# the API process at all. The call site inside upload_video() is unchanged, and
# the signature and return type still match the retired
# src.modelling.user_embedding.get_user_embedding.
from service.services.ml_client import get_user_embedding
from decimal import Decimal, ROUND_HALF_UP
from service.configuration.env import Env


env = Env()
log = setup_logging()


def build_matrix(
    candidates=np.array,
    ocean=np.array,
    vacancies=np.array):
    C = cosine_distances(candidates, ocean)
    P = cosine_distances(vacancies, ocean)
    final_matrix = C.dot(P.T) + (1 - C).dot(1 - P.T)
    return final_matrix, C, P


def filter_matrix(final_matrix, threshold=0.7):
    normalized_matrix = final_matrix.copy()
    row_min = normalized_matrix.min(axis=1).reshape(-1, 1)
    row_max = normalized_matrix.max(axis=1).reshape(-1, 1)
    denominator = row_max - row_min
    denominator[denominator == 0] = 1
    normalized_matrix = (normalized_matrix - row_min) / denominator
    normalized_matrix[normalized_matrix < threshold] = 0
    return normalized_matrix


# def similarity_start():
#     # Сперва достаем ембендинги OCEAN из базы данных
#     personalities = personality_services.get_all_personalities()
#     ocean = []
#     for personality in personalities:
#         if personality.Name in ['O', 'C', 'E', 'A', 'N']:
#             ocean.append(np.load(embedding_services.get_embedding_by_id(personality.EmbeddingID).Url))
#     ocean = np.vstack(ocean)

#     # Получаем все пользователи
#     users = user_services.get_all_users()
#     user_embeddings = []
#     for user in users:
#         info_candidate = info_candidate_services.get_info_candidate_by_id(user.InfoID)
#         user_embeddings.append(np.load(embedding_services.get_embedding_by_id(info_candidate.EmbeddingID)))
#     user_embeddings = np.vstack(user_embeddings)

#     # Получаем все вакансии
#     vacancies = vacancy_services.get_all_vacancies()
#     vacancy_embeddings = []
#     for vacancy in vacancies:
#         vacancy_embeddings.append(np.load(embedding_services.get_embedding_by_id(vacancy.EmbeddingID)))
#     vacancy_embeddings = np.vstack(vacancy_embeddings)

#     # Строим матрицу сходства
#     a, C, P = build_matrix(user_embeddings, ocean, vacancy_embeddings)
#     b = filter_matrix(a)

#     # Запись данных в таблицу PersonalityCandidates (сходства личности и кандидата)
#     for i, user in enumerate(users):
#         for j, personality in enumerate(personalities):
#             similarity = C[i, j]
#             if similarity >= 0.7:  # Пороговое значение сходства
#                 # Создаем запись в базе данных
#                 personality_candidate = PersonalityCandidates(
#                     PersonalityID=personality.ID,
#                     UserID=user.ID,
#                     Distance=similarity
#                 )
#                 personality_candidate_services.create_personality_candidate(personality_candidate)

#     # Запись данных в таблицу PersonalityVacancies (сходства личности и вакансии)
#     for i, personality in enumerate(personalities):
#         for j, vacancy in enumerate(vacancies):
#             similarity = P[i, j]
#             if similarity >= 0.7:  # Пороговое значение сходства
#                 # Создаем запись в базе данных
#                 personality_vacancy = PersonalityVacancies(
#                     PersonalityID=personality.ID,
#                     VacancyID=vacancy.ID,
#                     Distance=similarity
#                 )
#                 personality_services.create_personality_vacancy(personality_vacancy)

#     # Запись данных в таблицу CandidateVacancies (сходства кандидата и вакансии)
#     for i, user in enumerate(users):
#         for j, vacancy in enumerate(vacancies):
#             similarity = b[i, j]
#             if similarity >= 0.7:  # Пороговое значение сходства
#                 # Создаем запись в базе данных
#                 candidate_vacancy = CandidateVacancies(
#                     UserID=user.ID,
#                     VacancyID=vacancy.ID,
#                     Distance=similarity
#                 )
#                 vacancy_services.create_candidate_vacancy(candidate_vacancy)



def similarity_for_user(user_id: int):
    """
    Функция для вычисления и записи сходств для одного конкретного пользователя.
    """
    # Сперва достаем ембендинги OCEAN из базы данных
    personalities = personality_services.get_all_personalities()
    ocean = []
    for personality in personalities:
        if personality.Name in ['O', 'C', 'E', 'A', 'N']:  # Предполагаем, что эти личности содержат нужные эмбеддинги
            ocean.append(np.load(embedding_services.get_embedding_by_id(personality.EmbeddingID).Url))
    ocean = np.vstack(ocean)

    # Получаем данные по конкретному пользователю
    user = user_services.get_user_by_id(user_id)
    info_candidate = info_candidate_services.get_info_candidate_by_id(user.InfoID)
    user_embedding = np.load(f"./data/{embedding_services.get_embedding_by_id(info_candidate.EmbeddingID).Url}")

    # Получаем все вакансии
    vacancies = vacancy_services.get_all_vacancies()
    vacancy_embeddings = []
    for vacancy in vacancies:
        vacancy_embeddings.append(np.load(embedding_services.get_embedding_by_id(vacancy.EmbeddingID).Url))
    vacancy_embeddings = np.vstack(vacancy_embeddings)

    # Строим матрицу сходства для пользователя, личностей и вакансий
    a, C, P = build_matrix(np.vstack([user_embedding]), ocean, vacancy_embeddings)
    b = filter_matrix(a)

    # Запись данных в таблицу PersonalityCandidates (сходства личности и кандидата)
    for j, personality in enumerate(personalities):
        similarity = C[0, j]  # Для одного пользователя индекс 0
        if similarity >= 0.7:  # Пороговое значение сходства
            # Создаем запись в базе данных
            personality_candidate = PersonalityCandidates(
                personality_id=personality.ID,
                user_id=user.ID,
                distance=min(Decimal(str(similarity)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), Decimal("0.999999"))
            )
            personality_candidate_services.create_personality_candidate(personality_candidate)

    # Запись данных в таблицу PersonalityVacancies (сходства личности и вакансии)
    for similarity, vacancy in zip(P[0, :], vacancies):
        if similarity >= 0.7:  # Пороговое значение сходства
            # Создаем запись в базе данных
            personality_vacancy = PersonalityVacancies(
                personality_id=personalities[j].ID,  # Привязываем к личности, с которой считано сходство
                vacancy_id=vacancy.ID,
                distance=min(Decimal(str(similarity)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), Decimal("0.999999"))
            )
            personality_vacancy_services.create_personality_vacancy(personality_vacancy)

    # Запись данных в таблицу CandidateVacancies (сходства кандидата и вакансии)
    for j, vacancy in enumerate(vacancies):
        similarity = b[0, j]  # Для одного пользователя индекс 0
        if similarity >= 0.7:  # Пороговое значение сходства
            # Создаем запись в базе данных
            candidate_vacancy = CandidateVacancies(
                user_id=user.ID,
                vacancy_id=vacancy.ID,
                distance=min(Decimal(str(similarity)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), Decimal("0.999999"))
            )
            candidate_vacancy_services.create_candidate_vacancy(candidate_vacancy)

    return C



async def upload_video(file,
                       user_id,
                       name,
                       phone,
                       date_birth,
                       gender):

    if date_birth is not None:
        date_birth = f"{date_birth}"
    # Загружаем видео на сервер
    unique_file = await write_file_into_server("files", file)
    # Записываем информацию в базу данных
    file_entity = file_services.create_file(Files(url=f"data/files/{unique_file}"))
    # Обрабатываем видео для получения эмбендинга
    video_embedding = get_user_embedding(file_entity.Url)
    # Сохраняем файл ембендинга на сервер
    unique_embedding = write_embedding_into_server("users", video_embedding)
    # Записываем информацию в базу данных
    embedding_entity = embedding_services.create_embedding(Embeddings(url=f"embeddings/users/{unique_embedding}"))
    # Записать данные в info_candidate
    user = user_services.get_user_by_id(user_id)
    log.info(user)
    if user.InfoID is not None:
        info_candidate = info_candidate_services.get_info_candidate_by_id(user.InfoID)
        info_candidate.Name = name
        info_candidate.Phone = phone
        info_candidate.DateBirth = date_birth
        info_candidate.Gender = gender
        info_candidate = info_candidate_services.update_info_candidate(user.InfoID, info_candidate.model_dump(by_alias=True))
        info_candidate = info_candidate_services.get_info_candidate_by_id(user.InfoID)
    else:
        info_candidate = info_candidate_services.create_info_candidate(InfoCandidates(
            name=name,
            phone=phone,
            gender=gender,
            date_birth=date_birth,
            file_id=file_entity.ID,
            embedding_id=embedding_entity.ID
        ))
    # Обновить данные профиля
    user.InfoID = info_candidate.ID
    user.CreatedAt = f"{user.CreatedAt}"
    user.Type = user.Type.value
    user.Role = user.Role.value
    user_services.update_user(user.ID, user.model_dump(by_alias=True))
    # try:
    #     C = similarity_for_user(user.ID)
    # except Exception as ex:
    #     log.exception(ex)

    return {
        "email": user.Email,
        "name": info_candidate.Name,
        "phone": info_candidate.Phone,
        "gender": info_candidate.Gender,
        "date_birth": f"{info_candidate.DateBirth}",
        "video_url": return_url_object(file_entity.Url),
        # "C": C.cpu().numpy().to_list()
    }



def upload_vacancy(name,
                   description,
                   salary,
                   skill):


    return {"message": "test"}




def get_all_vacancies(user_id):
    # Получаем информацию пользователя
    user = user_services.get_user_by_id(user_id)
    candidate_vacancies = candidate_vacancy_services.get_all_candidate_vacancies()
    user_vacancies = []
    # The loop variable was misspelled 'smaple' while the body read 'sample.',
    # so this branch could never work (NameError on the first row). Renaming the
    # variable - not aliasing it - is the only fix that keeps the body correct.
    for sample in candidate_vacancies:
        if sample.UserID == user_id:
            user_vacancies.append(sample.model_dump())
    # `sort(reverse=True)` on a list of DICTS raised
    # "TypeError: '<' not supported between instances of 'dict' and 'dict'" the
    # moment a candidate had two or more rows - a latent bug recorded in
    # docs/KNOWN_GAPS.md and now fixed, because a refactor that preserves a
    # guaranteed 500 is not a refactor. `distance` is the similarity score the
    # matrix writes (decimal(6,6) in database/HireSnap.sql), so the ordering the
    # caller wants is "closest first".
    user_vacancies.sort(key=lambda row: row.get("distance") or 0, reverse=True)
    return user_vacancies


def get_all_candidates(vacancy_id):
    # Получаем информацию пользователя
    vacancy = vacancy_services.get_vacancy_by_id(vacancy_id)
    candidate_vacancies = candidate_vacancy_services.get_all_candidate_vacancies()
    vacancy_candidates = []
    for sample in candidate_vacancies:  # same misspelling as above
        if sample.VacancyID == vacancy_id:
            vacancy_candidates.append(sample.model_dump())
    vacancy_candidates.sort(key=lambda row: row.get("distance") or 0, reverse=True)
    return vacancy_candidates

#!/bin/bash
# =============================================================================
# HISTORICAL RECORD - this file no longer runs the pipeline.
# =============================================================================
#
# The OCEAN/MBTI pipeline this script drove has moved out of backend/ into the
# "ml" submodule (ml/service/, served over HTTP by ml/service/app.py). The
# modules it used to call - src/modelling/video_to_text.py, get_text_embedding.py,
# get_frames.py, get_video_embedding.py, get_common_embedding.py, submit.py and
# mbti.py - were deleted together with src/modelling/; cosmos_tokenizer/ went with
# them. Every command below is therefore commented out, and running this file
# prints a notice and exits non-zero on purpose.
#
# What replaced it:
#   * embeddings and transcription are HTTP routes on the sidecar
#       POST /v1/embed/video   raw video bytes  -> {vector, dim, model}
#       POST /v1/embed/text    {"text": "..."}  -> {vector, dim, model}
#       POST /v1/transcribe    raw media bytes  -> {text, model}
#     The address is ML_SERVICE_URL (http://ml:8080 inside the stack,
#     http://127.0.0.1:8080 on a checkout). The canonical description is
#     ml/README.md, not this file.
#   * the OCEAN/MBTI ranking step - what submit.py and mbti.py computed - did NOT
#     become a route. It is business logic and stayed in the API, as
#     build_matrix() in backend/src/services/main_services.py. There is no
#     command-line equivalent any more: the API computes it per candidate when a
#     video is uploaded.
#
# Why the dead commands are kept: they record the ORDER the old batch flow ran in
# and the arguments each module took. That order is still the order the sidecar
# executes internally, so the block below is useful when reading ml/service/. It
# is a record, not runnable documentation.
#
# To process videos today, upload a candidate video through the API, or call the
# sidecar routes directly with curl.
# =============================================================================

# Проверяем наличие аргументов
if [ -z "$1" ]; then
  echo "Usage: $0 <path_to_main_folder>"
  exit 1
fi

# Основная директория
MAIN_FOLDER=$1

# Поддиректории и файлы
VIDEO_FOLDER="$MAIN_FOLDER/test_data"
VACANCIES_TABLE="data/tables/vacancies.csv"
VACANCIES_FOLDER="$MAIN_FOLDER/vacancies"
PERSONALITY_TABLE="data/tables/personalities.csv"
PERSONALITY_FOLDER="$MAIN_FOLDER/personalities"
FRAMES_FOLDER="$MAIN_FOLDER/frames"
TEMP_AUDIO_FOLDER="$MAIN_FOLDER/temp_audio"
TRANSCRIPTION_PATH="$MAIN_FOLDER/transcription_speech.csv"
USERS_FOLDER="$MAIN_FOLDER/users"
SUBMIT_OUTPUT="$MAIN_FOLDER/ocean_results_text.pkl"
MBTI_OUTPUT="$MAIN_FOLDER/mbti_results_text.pkl"

# ---- Everything below is the retired pipeline, kept as history. --------------
# The two steps that were still LIVE before the split (submit.py and mbti.py) are
# commented out here as well: they were the only lines that could still execute,
# and both import modules that no longer exist.

# # Шаг 1: Получение текстовых эмбеддингов
# echo "Step 1: Extracting audio, transcribing, and generating text embeddings..."

# # Транскрибируем видео и сохраняем текст
# python src/modelling/video_to_text.py --video_folder_path "$VIDEO_FOLDER" --table_path "$TRANSCRIPTION_PATH" --audio_folder "$TEMP_AUDIO_FOLDER"

# # Генерация текстовых эмбеддингов для кандидатов
# python src/modelling/get_text_embedding.py --path_to_texts "$TRANSCRIPTION_PATH" --output_folder "$USERS_FOLDER/text_embeddings"

# # Генерация текстовых эмбеддингов для вакансий
# python src/modelling/get_text_embedding.py --path_to_texts "$VACANCIES_TABLE" --output_folder "$VACANCIES_FOLDER/embeddings" --disable_candidates

# # Генерация текстовых эмбеддингов для OCEAN (личностных черт)
# python src/modelling/get_text_embedding.py --path_to_texts "$PERSONALITY_TABLE" --output_folder "$PERSONALITY_FOLDER/embeddings" --disable_candidates

# # Шаг 2: Получение видео эмбеддингов
# echo "Step 2: Generating video embeddings..."

# # Извлекаем фреймы
# python src/modelling/get_frames.py "$VIDEO_FOLDER" "$FRAMES_FOLDER"

# # Экспортируем путь к токенайзеру
# export PYTHONPATH=$PYTHONPATH:~/HireSnap/cosmos_tokenizer

# # Извлекаем эмбеддинги для видео пользователей
# python src/modelling/get_video_embedding.py --data_dir "$FRAMES_FOLDER" --batch_size 1 --weights_path "src/weights/Unet3D.pth" --model_state "inference" --embeddings_path "$USERS_FOLDER/video_embeddings"

# # Шаг 3: Получение общих эмбеддингов для пользователя
# echo "Step 3: Generating common embeddings..."

# # Объединяем текстовые и видео эмбеддинги
# python src/modelling/get_common_embedding.py --video_embedding_folder "$USERS_FOLDER/video_embeddings" --text_embedding_folder "$USERS_FOLDER/text_embeddings" --output_folder "$USERS_FOLDER/embeddings"

# # Шаг 4: Вычисление сходства и генерация submit файла
# echo "Step 4: Calculating similarity and generating submission file..."

# # Запускаем расчет сходства с личностными чертами и сохраняем результат в .pkl файл
# python src/modelling/submit.py "$USERS_FOLDER/text_embeddings" "$PERSONALITY_FOLDER/embeddings" --metric 'cosine' --o "$SUBMIT_OUTPUT"

# # Шаг 5: Вычисление MBTI и сохранение в файл
# echo "Step 5: Calculating MBTI and generating MBTI file..."

# python src/modelling/mbti.py "$USERS_FOLDER/text_embeddings" "$PERSONALITY_FOLDER/embeddings" --metric 'cosine' --o "$MBTI_OUTPUT"

# ---- Retired pipeline ends here. --------------------------------------------

echo "Nothing was executed: the pipeline moved to the ml/ sidecar (see the header)."
echo "  embeddings/transcription: POST /v1/embed/video, /v1/embed/text, /v1/transcribe on \$ML_SERVICE_URL"
echo "  OCEAN/MBTI ranking:      build_matrix() in backend/src/services/main_services.py"
echo "  contract:                ml/README.md"
exit 2

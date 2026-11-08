import os
import uuid
import numpy as np
from aiofiles import open as aio_open
from service.configuration.env import Env
env = Env()


async def write_file_into_server(name_object: str, file):
    # Получаем расширение файла
    file_extension = file.filename.split('.')[-1]
    # Создаем уникальное имя файла
    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    # Записываем путь к файлу
    file_location = os.path.join(env.__getattr__("DATA_PATH"), f"{name_object}", unique_filename)
    # Проверяем существует ли папка, в которой храняться файлы
    os.makedirs(os.path.join(env.__getattr__("DATA_PATH"), f"{name_object}"), exist_ok=True)
    # Открывааем файл и записываем данные изображения
    async with aio_open(file_location, "wb") as buffer:
        await buffer.write(await file.read())
    return unique_filename


# np.ndarray, not np.array: the latter is a constructor function, so using it as
# an annotation made mypy fail with "np.array is not valid as a type" and told a
# reader nothing about the value.
def write_embedding_into_server(name_object: str, embedding: np.ndarray):
    # Создаем уникальное имя файла
    unique_embedding = f"{uuid.uuid4()}.npy"
    # Сохраняем файл на сервер
    embedding_location = os.path.join(env.__getattr__("DATA_PATH"), f"embeddings/{name_object}", unique_embedding)
    # Проверяем существует ли папка, в которой храняться файлы
    os.makedirs(os.path.join(env.__getattr__("DATA_PATH"), f"embeddings/{name_object}"), exist_ok=True)
    # Cохраняем как .npy файл
    np.save(embedding_location, embedding)
    return unique_embedding

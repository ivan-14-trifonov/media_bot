# Используем официальный образ Python 3.11 (оптимизированная версия slim)
FROM python:3.11-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код приложения
COPY app.py .

# Открываем порт 5000 для доступа к приложению
EXPOSE 5000

# Запускаем приложение
CMD ["python", "app.py"]
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY image_platform /app/image_platform
COPY platform_app /app/platform_app
COPY manage.py /app/manage.py

RUN pip install --no-cache-dir .

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

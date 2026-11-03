# One image for the web app, the worker and the mock GitLab (prototype).
# PYTHON_IMAGE lets a network that cannot reach Docker Hub pull through a mirror,
# e.g. --build-arg PYTHON_IMAGE=registry.example.com/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

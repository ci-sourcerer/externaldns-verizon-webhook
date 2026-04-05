# syntax=docker/dockerfile:1

FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt app.py ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt && rm requirements.txt

EXPOSE 8888

USER 1000
CMD ["python", "app.py"]

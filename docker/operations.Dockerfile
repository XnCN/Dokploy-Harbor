# syntax=docker/dockerfile:1
ARG RESTIC_IMAGE
ARG PYTHON_IMAGE
FROM ${RESTIC_IMAGE} AS restic
FROM ${PYTHON_IMAGE} AS runtime
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
WORKDIR /app
COPY harbor/*.py /app/
COPY images.lock.json /app/images.lock.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/operations.py"]
FROM runtime AS test
COPY tests /tests
COPY vendor/harbor.yml.tmpl /app/harbor.yml.tmpl
RUN python3 -m unittest discover -s /tests -v

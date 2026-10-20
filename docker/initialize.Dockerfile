# syntax=docker/dockerfile:1
ARG PREPARE_IMAGE
FROM ${PREPARE_IMAGE}
USER root
COPY harbor/common.py harbor/initialize.py /app/
COPY images.lock.json /app/images.lock.json
COPY vendor/harbor.yml.tmpl /app/harbor.yml.tmpl
ENTRYPOINT ["python3", "/app/initialize.py"]

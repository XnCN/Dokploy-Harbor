#!/bin/sh
set -eu
node tools/check.mjs
python_image=$(node -e 'const x=require("./images.lock.json").images.python;console.log(x.repository+":"+x.tag+"@"+x.digest)')
restic_image=$(node -e 'const x=require("./images.lock.json").images.restic;console.log(x.repository+":"+x.tag+"@"+x.digest)')
docker build --target test -f docker/operations.Dockerfile \
  --build-arg "PYTHON_IMAGE=$python_image" --build-arg "RESTIC_IMAGE=$restic_image" .

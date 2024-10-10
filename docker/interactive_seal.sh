#!/bin/bash

IMAGE=${1:-"alvin-lddl"}

docker run \
  --name alvin_lddl \
  --gpus all \
  --init \
  -it \
  --rm \
  --network=host \
  --ipc=host \
  -v $PWD:/workspace/lddl \
  -v $HOME/datasets:/datasets \
  ${IMAGE} 
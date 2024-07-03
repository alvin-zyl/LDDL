#!/bin/bash

echo "Start preprocessing"
docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "/data/alvinliu:/dataset" lddl \
    mpirun \
    -np 64 \
    -x LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so \
    --oversubscribe \
    --allow-run-as-root \
        /workspace/lddl/preprocess_codebert_pretrain \
        --schedule mpi \
        --target-seq-length 512 \
        --code /dataset/codebert/source \
        --sink /dataset/codebert/pretrain \
        --vocab-file /workspace/lddl/lddl/dask/bert/vocab \
        --num-blocks 4096 \
        --seed 42
echo "Finished preprocessing"

echo "Start balance"
  docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "/data/alvinliu:/dataset" lddl \
    mpirun \
    --oversubscribe \
    --allow-run-as-root \
    -np 64 \
        balance_dask_output \
          --indir /dataset/codebert/pretrain \
          --num-shards 4096

  echo "Finished balance"
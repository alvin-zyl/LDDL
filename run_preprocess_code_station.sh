#!/bin/bash

echo "Start preprocessing phase 1"
docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "/data/alvinliu:/dataset" lddl \
    mpirun \
    -np 64 \
    -x LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so \
    --oversubscribe \
    --allow-run-as-root \
        /workspace/lddl/preprocess_codebert_pretrain \
        --schedule mpi \
        --target-seq-length 128 \
        --code /dataset/codebert/source \
        --sink /dataset/codebert/pretrain/phase1 \
        --vocab-file /workspace/lddl/codebert_52000/vocab.txt \
        --num-blocks 4096 \
        --seed 42
echo "Finished preprocessing phase 1"

echo "Start balance phase 1"
  docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "/data/alvinliu:/dataset" lddl \
    mpirun \
    --oversubscribe \
    --allow-run-as-root \
    -np 64 \
        balance_dask_output \
          --indir /dataset/codebert/pretrain/phase1 \
          --num-shards 4096

  echo "Finished balance phase 1"

  echo "Start preprocessing phase 2"
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
        --sink /dataset/codebert/pretrain/phase2 \
        --vocab-file /workspace/lddl/codebert_52000/vocab.txt \
        --num-blocks 4096 \
        --seed 42
echo "Finished preprocessing phase 2"

echo "Start balance phase 2"
  docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "/data/alvinliu:/dataset" lddl \
    mpirun \
    --oversubscribe \
    --allow-run-as-root \
    -np 64 \
        balance_dask_output \
          --indir /dataset/codebert/pretrain/phase2 \
          --num-shards 4096

  echo "Finished balance phase 2"
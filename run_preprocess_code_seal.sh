#!/bin/bash

echo "Start preprocessing phase 1"
docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "$HOME/datasets:/datasets" alvin-lddl \
    mpirun \
    -np 64 \
    -x LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so \
    --oversubscribe \
    --allow-run-as-root \
        /workspace/lddl/preprocess_codebert_pretrain \
        --schedule mpi \
        --target-seq-length 128 \
        --sample-ratio 1.0 \
        --code /datasets/codebert/source \
        --sink /datasets/codebert/pretrain/phase1 \
        --bin-size 32 \
        --num-blocks 4096 \
        --seed 42
echo "Finished preprocessing phase 1"

echo "Start balance phase 1"
  docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "$HOME/datasets:/datasets" alvin-lddl \
    mpirun \
    --oversubscribe \
    --allow-run-as-root \
    -np 64 \
        balance_dask_output \
          --indir /datasets/codebert/pretrain/phase1 \
          --num-shards 4096

echo "Finished balance phase 1"

echo "Start preprocessing phase 2"
docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "$HOME/datasets:/datasets" alvin-lddl \
    mpirun \
    -np 64 \
    -x LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so \
    --oversubscribe \
    --allow-run-as-root \
        /workspace/lddl/preprocess_codebert_pretrain \
        --schedule mpi \
        --target-seq-length 512 \
        --sample-ratio 1.0 \
        --code /datasets/codebert/source \
        --sink /datasets/codebert/pretrain/phase2 \
        --bin-size 64 \
        --num-blocks 4096 \
        --seed 42
echo "Finished preprocessing phase 2"

echo "Start balance phase 2"
  docker run --rm --shm-size="4096m" -v "$PWD:/workspace/lddl" -v "$HOME/datasets:/datasets" alvin-lddl \
    mpirun \
    --oversubscribe \
    --allow-run-as-root \
    -np 64 \
        balance_dask_output \
          --indir /datasets/codebert/pretrain/phase2 \
          --num-shards 4096

echo "Finished balance phase 2"
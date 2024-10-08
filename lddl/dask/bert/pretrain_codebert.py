#
# SPDX-FileCopyrightText: NVIDIA CORPORATION & AFFILIATES
# Copyright (c) 1993-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
#

import argparse
import dask
import dask.bag as db
import dask.distributed
import functools
import json
import nltk
import numpy as np
import os
import pyarrow as pa
import random
import time
import transformers
from collections import deque, namedtuple

from ..readers import (
    read_code,
    read_wikipedia,
    read_books,
    read_common_crawl,
    split_id_text,
    estimate_block_size,
    split_id_code_docstring,
)
from lddl.utils import (
    expand_outdir_and_mkdir,
    attach_bool_arg,
    serialize_np_array,
    deserialize_np_array,
)
from lddl.download.utils import parse_str_of_num_bytes

from .binning import to_textfiles_binned, to_dataframe_binned, to_parquet_binned
from typing import Union, List


class Sentence:

    def __init__(self, tokens):
        self._tokens = tokens

    def __repr__(self):
        return "Sentence(_tokens={})".format(self._tokens)

    def __len__(self):
        return len(self._tokens)


class Document:

    def __init__(self, sentences):
        self._sentences = sentences

    def __repr__(self):
        return "Document(_sentences={})".format(self._sentences)

    def __len__(self):
        return len(self._sentences)

    def __getitem__(self, idx):
        return self._sentences[idx]


class CodePair:

    def __init__(self, code_pair_id, codes: Document, docstrings: Document):
        self._id = code_pair_id
        self._codes = codes
        self._docstrings = docstrings

        assert isinstance(codes, Document)
        assert isinstance(docstrings, Document)

    def __repr__(self):
        return "Code(_id={}, _codes={}, _docstring={})".format(
            self._id, self._codes, self._docstrings
        )

    def __len__(self):
        return len(self._codes)

    def __getitem__(self, idx) -> Sentence:
        return self._codes[idx]

    def get_doc_segment(self, idx) -> Sentence:
        return self._docstrings[idx]

    @property
    def num_doc_segments(self):
        return len(self._docstrings)


def split_code(code, separator="\n"):
    return code.split(separator)


def _get_code_pairs(bag_texts: db.Bag, tokenizer, max_length=512):

    def _tokenize(s):
        return tokenizer.tokenize(s, max_length=max_length, truncation=True)

    def _to_code_pair(raw_text) -> CodePair:
        code_pair_id, docstring, code = split_id_code_docstring(raw_text)
        doc_strs = filter(
            None,
            map(
                lambda s: s.strip(),
                split_code(docstring),
            ),
        )
        code_strs = filter(
            None,
            map(
                lambda s: s.strip(),
                split_code(code),
            ),
        )

        doc_sentences = (
            Sentence(tuple(tokens))
            for tokens in (_tokenize(doc_str) for doc_str in doc_strs)
            if len(tokens) > 0
        )
        code_sentences = (
            Sentence(tuple(tokens))
            for tokens in (_tokenize(code_str) for code_str in code_strs)
            if len(tokens) > 0
        )

        document = CodePair(
            code_pair_id,
            Document(tuple(code_sentences)),
            Document(tuple(doc_sentences)),
        )
        return document

    return bag_texts.map(_to_code_pair).filter(lambda d: len(d) > 0)


def _shuffle_bag_texts(bag_texts):

    return (
        bag_texts.map(
            lambda text: {
                "text": text,
                "on": random.random(),
            }
        )
        .to_dataframe(
            meta={
                "text": str,
                "on": float,
            }
        )
        .shuffle(
            "on",
            ignore_index=True,
        )
        .sample(frac=1.0)
        .to_bag()
        .map(lambda t: t[0])
    )


def _cut(lcut, tokens, rcut):
    if random.random() > 0.5:
        rcut.appendleft(tokens.pop())
    else:
        lcut.append(tokens.popleft())


def _is_following_subword(word):
    return word[:2] == "##" and len(word) > 2 and word[3:].isalpha()


def _adjust(lcut, tokens, rcut):
    inclusive = random.random() > 0.5
    while len(tokens) > 0 and _is_following_subword(tokens[0]):
        if inclusive:
            if len(lcut) == 0:
                break
            tokens.appendleft(lcut.pop())
        else:
            lcut.append(tokens.popleft())
    inclusive = random.random() > 0.5
    while len(rcut) > 0 and _is_following_subword(rcut[0]):
        if inclusive:
            tokens.append(rcut.popleft())
        else:
            if len(tokens) == 0:
                break
            rcut.appendleft(tokens.pop())


def _truncate(tokens_A, tokens_B, max_length):
    tokens_A, tokens_B = deque(tokens_A), deque(tokens_B)
    lcut_A, rcut_A = deque([]), deque([])
    lcut_B, rcut_B = deque([]), deque([])

    # Truncate each sequence into 3 pieces: lcut, tokens, rcut
    while len(tokens_A) + len(tokens_B) > max_length:
        if len(tokens_A) > len(tokens_B):
            _cut(lcut_A, tokens_A, rcut_A)
        else:
            _cut(lcut_B, tokens_B, rcut_B)

    _adjust(lcut_A, tokens_A, rcut_A)
    _adjust(lcut_B, tokens_B, rcut_B)
    return tuple(tokens_A), tuple(tokens_B)


def _truncate_seq(tokens, max_num_tokens):
    while True:
        total_length = len(tokens)
        if total_length <= max_num_tokens:
            break

        # We want to sometimes truncate from the front and sometimes from the
        # back to add more randomness and avoid biases.
        if random.random() < 0.5:
            del tokens[0]
        else:
            tokens.pop()


def _truncate_seq_deter(tokens, max_num_tokens):
    while True:
        total_length = len(tokens)
        if total_length <= max_num_tokens:
            break

        tokens.pop()


def _truncate_seq_pair(tokens_a, tokens_b, max_num_tokens):
    """Truncates a pair of sequences to a maximum sequence length."""
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_num_tokens:
            break

        trunc_tokens = tokens_a if len(tokens_a) > len(tokens_b) else tokens_b
        assert len(trunc_tokens) >= 1

        # We want to sometimes truncate from the front and sometimes from the
        # back to add more randomness and avoid biases.
        if random.random() < 0.5:
            del trunc_tokens[0]
        else:
            trunc_tokens.pop()


MaskedLmInstance = namedtuple("MaskedLmInstance", ["index", "label"])


def create_masked_lm_predictions(tokens_a, tokens_b, masked_lm_ratio, vocab_words):
    """Creates the predictions for the masked LM objective."""
    num_tokens_a, num_tokens_b = len(tokens_a), len(tokens_b)
    tokens = (
        ["[CLS]"]
        + ((tokens_a + ["[SEP]"]) if tokens_a else tokens_a)
        + tokens_b
        + ["[SEP]"]
    )

    cand_indexes = []
    for i, token in enumerate(tokens):
        if token == "[CLS]" or token == "[SEP]":
            continue
        cand_indexes.append(i)

    random.shuffle(cand_indexes)

    output_tokens = list(tokens)

    num_to_predict = max(1, int(round(len(tokens) * masked_lm_ratio)))

    masked_lms = []
    covered_indexes = set()
    for index in cand_indexes:
        if len(masked_lms) >= num_to_predict:
            break
        if index in covered_indexes:
            continue
        covered_indexes.add(index)

        masked_token = None
        # 80% of the time, replace with [MASK]
        if random.random() < 0.8:
            masked_token = "[MASK]"
        else:
            # 10% of the time, keep original
            if random.random() < 0.5:
                masked_token = tokens[index]
            # 10% of the time, replace with random word
            else:
                masked_token = vocab_words[random.randint(0, len(vocab_words) - 1)]

        output_tokens[index] = masked_token

        masked_lms.append(MaskedLmInstance(index=index, label=tokens[index]))

    masked_lms = sorted(masked_lms, key=lambda x: x.index)

    masked_lm_positions = []
    masked_lm_labels = []
    for p in masked_lms:
        masked_lm_positions.append(p.index)
        masked_lm_labels.append(p.label)

    return (
        output_tokens[1 : 1 + num_tokens_a],
        output_tokens[2 + num_tokens_a : 2 + num_tokens_a + num_tokens_b],
        masked_lm_positions,
        masked_lm_labels,
    )


def create_pairs_from_document(
    all_documents: List[CodePair],
    document_index: int,
    max_seq_length=128,
    short_seq_prob=0.1,
    masking=False,
    masked_lm_ratio=0.15,
    vocab_words=None,
):
    """Create a pair for a single document."""
    document = all_documents[document_index]

    # Account for [CLS], [SEP], [SEP] if docstring exists, else [CLS], [SEP]
    special_token_length = 3 if document.num_doc_segments else 2
    max_num_tokens = max_seq_length - special_token_length
    max_doc_seq_length = 64 if max_seq_length >= 512 else 32

    # We *usually* want to fill up the entire sequence since we are padding
    # to `max_seq_length` anyways, so short sequences are generally wasted
    # computation. However, we *sometimes*
    # (i.e., short_seq_prob == 0.1 == 10% of the time) want to use shorter
    # sequences to minimize the mismatch between pre-training and fine-tuning.
    # The `target_seq_length` is just a rough target however, whereas
    # `max_seq_length` is a hard limit.
    target_seq_length = max_num_tokens
    short_seq_p = random.random()

    i = 0
    doc_tokens = []
    current_chunk: List[Sentence] = []
    current_length = 0
    # In 10% of the time, we directly use the first sentence as doc
    if document.num_doc_segments and short_seq_p < short_seq_prob:
        doc_tokens.extend(document.get_doc_segment(0)._tokens)
    else:
        while i < document.num_doc_segments:
            segment = document.get_doc_segment(i)
            current_chunk.append(segment)
            current_length += len(segment)
            if i == len(document) - 1 or current_length > max_doc_seq_length:
                if current_chunk:
                    if current_length > max_doc_seq_length and len(current_chunk) > 1:
                        end = len(current_chunk) - 1
                    else:
                        end = len(current_chunk)

                    for j in range(end):
                        doc_tokens.extend(current_chunk[j]._tokens)

                    _truncate_seq(doc_tokens, max_doc_seq_length)

                    break

            i += 1

    # For the same function, we split the code by "\n" into sentences,
    # the docstring could be paired with different parts of the complete code
    instances = []
    current_chunk: List[Sentence] = []
    doc_length = len(doc_tokens)
    current_length = doc_length  # Current length is alwasy on top of doc length

    i = 0
    while i < len(document):
        segment = document[i]
        current_chunk.append(segment)
        current_length += len(segment)
        if i == len(document) - 1 or current_length > target_seq_length:
            if current_chunk:
                if current_length > max_num_tokens and len(current_chunk) > 1:
                    stay_chunk_idx = [-1]
                else:
                    stay_chunk_idx = []

                code_tokens = []
                for j in range(len(current_chunk)):
                    code_tokens.extend(current_chunk[j]._tokens)

                _truncate_seq(code_tokens, max_num_tokens - doc_length)

                assert len(code_tokens) >= 1

                if not instances or len(code_tokens) >= 16:
                    instance = {
                        "id": document._id,
                        "doc": " ".join(doc_tokens),
                        "code": " ".join(code_tokens),
                        "num_tokens": len(doc_tokens)
                        + len(code_tokens)
                        + special_token_length,
                    }
                    instances.append(instance)

            current_chunk = [current_chunk[i] for i in stay_chunk_idx]
            current_length = (
                sum([len(item) for item in current_chunk]) if current_chunk else 0
            ) + doc_length
        i += 1

    return instances


def _get_pairs(
    code_path=None,
    target_seq_length=128,
    short_seq_prob=0.1,
    blocksize=None,
    num_blocks=None,
    duplicate_factor=1,
    sample_ratio=0.9,
    seed=12345,
    tokenizer=None,
    masking=False,
    masked_lm_ratio=0.15,
):
    vocab_words = tuple(tokenizer.vocab.keys())

    def _to_partition_pairs(partition_documents):
        partition_documents = tuple(partition_documents)
        partition_pairs = []
        for _ in range(duplicate_factor):
            for document_index in range(len(partition_documents)):
                partition_pairs.extend(
                    create_pairs_from_document(
                        partition_documents,
                        document_index,
                        max_seq_length=target_seq_length,
                        short_seq_prob=short_seq_prob,
                        masking=masking,
                        masked_lm_ratio=masked_lm_ratio,
                        vocab_words=vocab_words,
                    )
                )
        random.shuffle(partition_pairs)
        return partition_pairs

    if num_blocks is not None:
        if blocksize is not None:
            raise ValueError("Only one of num_blocks or blocksize needs to be set!")

    bags = []
    if code_path is not None:
        bags.append(read_code(code_path, sample_ratio=sample_ratio, sample_seed=seed))
    bag_texts = db.concat(bags)
    bag_texts = _shuffle_bag_texts(bag_texts)
    bag_documents = _get_code_pairs(bag_texts, tokenizer)
    return bag_documents.map_partitions(_to_partition_pairs)


def _save_parquet(
    pairs,
    path,
    bin_size=None,
    target_seq_length=128,
    masking=False,
):
    base_meta = {
        "id": str,
        "doc": str,
        "code": str,
        "num_tokens": int,
    }
    base_schema = {
        "id": pa.string(),
        "doc": pa.string(),
        "code": pa.string(),
        "num_tokens": pa.uint16(),
    }
    if bin_size is None:
        pairs.to_dataframe(meta=base_meta).to_parquet(
            path,
            engine="pyarrow",
            write_index=False,
            schema=base_schema,
        )
    else:
        nbins = target_seq_length // bin_size
        pairs.to_dataframe = to_dataframe_binned
        dfs = pairs.to_dataframe(
            pairs,
            meta=base_meta,
            bin_size=bin_size,
            nbins=nbins,
        )
        to_parquet_binned(
            dfs,
            path,
            nbins,
            engine="pyarrow",
            write_index=False,
            schema={
                **base_schema,
                "bin_id": pa.int64(),
            },
        )


def _save_txt(
    pairs,
    path,
    bin_size=None,
    target_seq_length=128,
    masking=False,
):
    pairs = pairs.map(
        lambda p: "{} [CLS] {} [SEP] {} [SEP] - {}".format(
            p["id"],
            p["doc"],
            p["code"],
            p["num_tokens"],
        )
    )
    if bin_size is None:
        db.core.to_textfiles(pairs, os.path.join(path, "*.txt"))
    else:
        nbins = target_seq_length // bin_size
        to_textfiles_binned(pairs, os.path.join(path, "*.txt"), bin_size, nbins)


def _save(
    pairs,
    path,
    output_format="parquet",
    bin_size=None,
    target_seq_length=128,
    masking=False,
):
    if output_format == "parquet":
        _save_parquet(
            pairs,
            path,
            bin_size=bin_size,
            target_seq_length=target_seq_length,
            masking=masking,
        )
    elif output_format == "txt":
        _save_txt(
            pairs,
            path,
            bin_size=bin_size,
            target_seq_length=target_seq_length,
            masking=masking,
        )

    else:
        raise ValueError("Format {} not supported!".format(output_format))


def main(args):
    dask.config.set({"distributed.comm.timeouts.connect": 60})

    if args.bin_size is not None:
        if args.bin_size > args.target_seq_length:
            raise ValueError("Please provide a bin size that is <= target-seq-length")
        if args.target_seq_length % args.bin_size != 0:
            raise ValueError(
                "Please provide a bin size that can divide the target "
                "sequence length."
            )

    if args.schedule == "mpi":
        from dask_mpi import initialize

        initialize(
            local_directory="/tmp/dask-worker-space", nanny=False, memory_limit=0.95
        )
        client = dask.distributed.Client()
    else:
        client = dask.distributed.Client(
            n_workers=args.local_n_workers,
            threads_per_worker=args.local_threads_per_worker,
        )

    nltk.download("punkt")
    tokenizer = transformers.AutoTokenizer.from_pretrained("microsoft/codebert-base")

    tic = time.perf_counter()
    pairs = _get_pairs(
        code_path=args.code,
        target_seq_length=args.target_seq_length,
        short_seq_prob=args.short_seq_prob,
        blocksize=args.block_size,
        num_blocks=args.num_blocks,
        duplicate_factor=args.duplicate_factor,
        sample_ratio=args.sample_ratio,
        seed=args.seed,
        tokenizer=tokenizer,
        masking=args.masking,
        masked_lm_ratio=args.masked_lm_ratio,
    )
    args.sink = expand_outdir_and_mkdir(args.sink)
    _save(
        pairs,
        args.sink,
        output_format=args.output_format,
        bin_size=args.bin_size,
        target_seq_length=args.target_seq_length,
        masking=args.masking,
    )
    print("Running the dask pipeline took {} s".format(time.perf_counter() - tic))


def attach_args(
    parser=argparse.ArgumentParser(
        """
LDDL Preprocessor for the BERT Pretraining Task

The LDDL preprocessor takes the text shards under 'source' subdirectories from
datasets, and preprocesses them into parquet files under the directory specified
by --sink. These parquet files are the input to the LDDL Load Balancer.

MPI is used to scale the LDDL preprocessor to multi-processes and multi-nodes.
MPI can be accessed in various ways. For example, we can access MPI via mpirun:
$ mpirun -c <number of processes per node> --oversubscribe --allow-run-as-root \\
    preprocess_bert_pretrain ...
We can also access MPI via SLURM in a HPC cluster:
$ srun -l --mpi=pmix --ntasks-per-node=<number of processes per node> \\
    preprocess_bert_pretrain ...

If you want to use jemalloc as the memory allocator, set the value of the
LD_PRELOAD environment variable to the path that points to libjemalloc.so. In
mpirun, we can set the '-x LD_PRELOAD=<path to libjemalloc.so>' flag. In SLURM,
we can set the '--export=ALL,LD_PRELOAD=<path to libjemalloc.so>' flag.

Since the LDDL preprocessor needs some data as input, at least one of
'--wikipedia', '--books' and '--common-crawl' needs to be set. For each dataset
that is fetched by a LDDL downloader, a 'source' subdirectory is expected to be
generated by the LDDL downloader. The path to the 'source' subdirectory should
be used as the value to each of the '--wikipedia', '--books' and
'--common-crawl' flags.

LDDL supports sequence binning. Given a bin size, the input sequences can be
categorized into several bins. For example, if --target-seq-length is set to 128
and --bin-size (which specifies the stride of the sequence length for each bin)
is set to 32, then we have 4 bins:
- sequences that has 1 to 32 tokens;
- sequences that has 33 to 64 tokens;
- sequences that has 65 to 96 tokens;
- sequences that has 97 to 128 tokens.
Each parquet file that the LDDL preprocessor generates only has sequences that
belong to one bin. During one training iteration, for all ranks, the input
mini-batch of data returned by the LDDL data loader only contains sequences that
belong to one bin, therefore, saving:
- wasted computation during the forward and backward passes on the padding
  tokens which need to be appended to sequences shorter than the longest
  sequence in a mini-batch;
- idle waiting time for the rank that uses a batch of sequences shorter than the
  longest sequence among all batches of all ranks.
The --bin-size flag needs to be set in order to enable sequence binning. Note
that, although a very small bin size would reduce the runtime as much as
possible, at the same time, it could lead to noticeable difference in the
convergence. A good bin size should be determined empirically by trading off
runtime with convergence impact.
"""
    ),
):
    parser.add_argument(
        "--schedule",
        type=str,
        default="mpi",
        choices=["mpi", "local"],
        help="Which scheduler is used to scale this LDDL pipeline. MPI should "
        "always be used and will be used by default. The local scheduler can only"
        " support a single node and is for debugging purpose only. Default: mpi",
    )
    defaults = {
        "--local-n-workers": os.cpu_count(),
        "--local-threads-per-worker": 1,
        "--code": None,
        "--wikipedia": None,
        "--books": None,
        "--common-crawl": None,
        "--sink": None,
        "--output-format": "parquet",
        "--wikipedia-lang": "en",
        "--target-seq-length": 128,
        "--short-seq-prob": 0.1,
        "--block-size": None,
        "--num-blocks": None,
        "--bin-size": None,
        "--sample-ratio": 0.9,
        "--seed": 12345,
        "--duplicate-factor": 1,
        "--vocab-file": "bert-large-uncased",
        "--masked-lm-ratio": 0.15,
    }
    parser.add_argument(
        "--local-n-workers",
        type=int,
        default=defaults["--local-n-workers"],
        help="The number of worker processes for the local scheduler; only used "
        "when --schedule=local . Default: {}".format(defaults["--local-n-workers"]),
    )
    parser.add_argument(
        "--local-threads-per-worker",
        type=int,
        default=defaults["--local-threads-per-worker"],
        help="The number of Python user-level threads per worker process for the "
        "local scheduler; only used when --schedule=local . Default: {}".format(
            defaults["--local-threads-per-worker"]
        ),
    )
    parser.add_argument(
        "--code",
        type=str,
        default=defaults["--code"],
        help="The path to the 'source' subdirectory for the code corpus. "
        "Default: {}".format(defaults["--code"]),
    )
    parser.add_argument(
        "--wikipedia",
        type=str,
        default=defaults["--wikipedia"],
        help="The path to the 'source' subdirectory for the Wikipedia corpus. "
        "Default: {}".format(defaults["--wikipedia"]),
    )
    parser.add_argument(
        "--books",
        type=str,
        default=defaults["--books"],
        help="The path to the 'source' subdirectory for the Toronto books corpus."
        " Default: {}".format(defaults["--books"]),
    )
    parser.add_argument(
        "--common-crawl",
        type=str,
        default=defaults["--common-crawl"],
        help="The path to the 'source' subdirectory for the Common Crawl news "
        "corpus. Default: {}".format(defaults["--common-crawl"]),
    )
    parser.add_argument(
        "--sink",
        type=str,
        default=defaults["--sink"],
        required=True,
        help="The path to the directory that stores the output (parquet or txt) "
        "files. Default: {}".format(defaults["--sink"]),
    )
    parser.add_argument(
        "--output-format",
        type=str,
        default=defaults["--output-format"],
        choices=["parquet", "txt"],
        help="The format of the output files. parquet should always be used and "
        "will be used by default. txt is for debugging purpose only. Default: "
        "{}".format(defaults["--output-format"]),
    )
    parser.add_argument(
        "--wikipedia-lang",
        type=str,
        default=defaults["--wikipedia-lang"],
        choices=["en", "zh"],
        help="The language type for the Wikipedia corpus. Currenly, only en is "
        "supported. Default: {}".format(defaults["--wikipedia-lang"]),
    )
    parser.add_argument(
        "--target-seq-length",
        type=int,
        default=defaults["--target-seq-length"],
        help="The targeted, maximum number of tokens for the "
        "'[CLS] A [SEP] B [SEP]' pair input sequences to the BERT Pretraining "
        "task. In the original BERT Pretraining task, Phase 1 requires "
        "--target-seq-length=128 whereas Phase 2 requires --target-seq-length=512"
        " . However, you can also be creative and set --target-seq-length to "
        "other positive integers greater than 3. Default: {}".format(
            defaults["--target-seq-length"]
        ),
    )
    parser.add_argument(
        "--short-seq-prob",
        type=float,
        default=defaults["--short-seq-prob"],
        help="If all samples are long sequences, BERT would overfit to only long "
        "sequences. Therefore, you need to introduce shorter sequences sometimes."
        " This flag specifies the probability of a random variable X with the "
        "Bernoulli distribution (i.e., X in {{0, 1}} and "
        "Pr(X = 1) = p = 1 - Pr(X = 0)), such that the value of X is drawn for "
        "every document/article and, when X = 1, the value of the targeted, "
        "maximum number of tokens for the '[CLS] A [SEP] B [SEP]' pair input "
        "sequences is a random integer following the uniform distribution "
        "between 2 and the value specified by --target-seq-length minus 3 (to "
        "exclude the '[CLS]' and 'SEP' tokens). Default: {}".format(
            defaults["--short-seq-prob"]
        ),
    )
    parser.add_argument(
        "--block-size",
        type=functools.partial(parse_str_of_num_bytes, return_str=False),
        default=defaults["--block-size"],
        metavar="n[KMG]",
        help="The size of each output parquet/txt shard. Since Dask cannot "
        "guarantee perfect load balance, this value is only used as an estimate. "
        "Only one of --block-size and --num-blocks needs to be set, since one "
        "value can be derived from the other. Default: {}".format(
            defaults["--block-size"]
        ),
    )
    parser.add_argument(
        "--num-blocks",
        type=int,
        default=defaults["--num-blocks"],
        help="The total number of the output parquet/txt shards. Since Dask "
        "cannot guarantee perfect load balance, this value is only used as an "
        "estimate. Only one of --block-size or --num-blocks needs to be set, "
        "since one value can be derived from the other. Default: {}".format(
            defaults["--num-blocks"]
        ),
    )
    parser.add_argument(
        "--bin-size",
        type=int,
        default=defaults["--bin-size"],
        help="If this flag is set, sequence binning is enabled. This flag "
        "specifies the stride of the sequence length for each bin. For example, "
        "if --bin-size is 64, the first bin contains sequences with 1 to 64 "
        "tokens, the second bin contains sequences with 65 to 128 tokens, and so "
        "on. The bin size has to be an integer that can divide the value of "
        "--target-seq-length. Default: {}".format(defaults["--bin-size"]),
    )
    parser.add_argument(
        "--sample-ratio",
        type=float,
        default=defaults["--sample-ratio"],
        help="Not all articles/documents have to be included into the pretraining"
        " dataset. This flag specifies the ratio of how many articles/documents "
        "are sampled from each corpus (i.e., --wikipedia, --books and "
        "--common_crawl). Default: {}".format(defaults["--sample-ratio"]),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=defaults["--seed"],
        help="The seed value for article/document sampling (i.e., "
        "--sample-ratio). Note that, the other part of this Dask pipeline is "
        "non-deterministic. Default: {}".format(defaults["--seed"]),
    )
    parser.add_argument(
        "--duplicate-factor",
        type=int,
        default=defaults["--duplicate-factor"],
        help="There is stochasticity when creating the '[CLS] A [SEP] B [SEP]' "
        "pair input sequences for each article/document, specifically from "
        "determining (1) the targeted, maximum number of tokens for each "
        "article/document, (2) which sentences are used as B, (3) how the "
        "sequence is truncated in case it is longer than the targeted, maximum "
        "number of tokens. Therefore, even the same article/document could lead "
        "to a different set of input sequences at different times. The "
        "--duplicate-factor flag specifies how many times the preprocessor "
        "repeats to create the input pairs from the same article/document. "
        "Default: {}".format(defaults["--duplicate-factor"]),
    )
    parser.add_argument(
        "--vocab-file",
        type=str,
        default=defaults["--vocab-file"],
        help="Either the path to a vocab file, or the model id of a pretrained "
        "model hosted inside a model repo on huggingface.co. "
        "Default: {}".format(defaults["--vocab-file"]),
    )
    attach_bool_arg(
        parser,
        "masking",
        default=False,
        help_str="LDDL supports both static and dynamic masking. Static masking "
        "means that the masking operation is applied by the preprocessor, thus, "
        "which and how tokens are masked is fixed during training. Dynamic "
        "masking refers to delaying the masking operation to the data loader, "
        "therefore, the same input sequence could be masked differently the next "
        "time it is returned by the data loader during a training iteration. In "
        "order to enable static masking, this flag needs to be set. This flag is"
        " not set by default.",
    )
    parser.add_argument(
        "--masked-lm-ratio",
        type=float,
        default=defaults["--masked-lm-ratio"],
        help="The ratio of the number of tokens to be masked when static masking "
        "is enabled (i.e., when --masking is set). Default: {}".format(
            defaults["--masked-lm-ratio"]
        ),
    )
    return parser


def console_script():
    main(attach_args().parse_args())

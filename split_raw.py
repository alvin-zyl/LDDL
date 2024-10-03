import pickle, os, gzip, json
from tqdm import tqdm


def get_files(split, lang):
    lang_files = [
        os.path.join(f"/datasets/codebert/{lang}/final/jsonl/{split}", file)
        for file in os.listdir(f"/datasets/codebert/{lang}/final/jsonl/{split}")
        if file.endswith("jsonl.gz")
    ]
    return lang_files


SPLIT_KEYS = ["train", "valid", "test"]
for lang in ["go", "java", "javascript", "python", "php", "ruby"]:
    print(f"Start split {lang}")
    lang_files, bimodal_data, lang_hash_set = {}, {}, {}
    lang_pkl_data = pickle.load(
        open(f"/datasets/codebert/{lang}_dedupe_definitions_v2.pkl", "rb")
    )
    for split in SPLIT_KEYS:
        lang_files[split] = get_files(split, lang)
        bimodal_data[split] = []
        for file in lang_files[split]:
            with gzip.open(file, "r") as f:
                bimodal_data[split] += f.readlines()
        bimodal_data[split] = [
            json.loads(str(item, encoding="utf-8")) for item in bimodal_data[split]
        ]
        lang_hash_set[split] = set([hash(item["code"]) for item in bimodal_data[split]])

    for split in SPLIT_KEYS:
        pkl_split_filtered = [
            (f"{lang}_{i}", item)
            for (i, item) in enumerate(lang_pkl_data)
            if (
                all(
                    [
                        hash(item["function"]) not in lang_hash_set[s]
                        for s in SPLIT_KEYS
                        if s != split
                    ]
                )
                if split == "train"
                else hash(item["function"]) in lang_hash_set[split]
            )
        ]
        pickle.dump(
            pkl_split_filtered, open(f"/datasets/codebert/{lang}_{split}.pkl", "wb")
        )

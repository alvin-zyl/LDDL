import pickle, os, gzip, json

SPLIT_KEYS = ["train", "valid", "test"]
for split in SPLIT_KEYS:
    codes, comments, ids = [], [], []
    for lang in ["python", "java", "javascript", "go", "php", "ruby"]:
        print(f"Extracting {lang}_{split}")
        lang_pkl_data = pickle.load(open(f"/datasets/codebert/{lang}_{split}.pkl", "rb"))
        num_bimodal, num_uni = 0, 0

        for id, instance in lang_pkl_data:
            codes.append(instance["function"])
            ids.append(id)
            comments.append(instance["docstring"])
            if not instance["docstring"]:
                num_uni += 1
            else:
                num_bimodal += 1
        print(
            f"Split: {split}, {lang}, bimodal data: {num_bimodal}, unimodal data: {num_uni}"
        )

    pickle.dump(
        (ids, comments, codes), open(f"/datasets/codebert/extracted_{split}.pkl", "wb")
    )
